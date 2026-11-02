"""Tdarr server/Node split, per-pod transcode scratch and GPU opt-in.

The shared GPU matrix in test_media_gpu covers charts whose container carries
no environment of its own; Tdarr configures itself entirely through env vars,
so its device wiring is checked here instead.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "media-server/tdarr"
AUTH = {"auth": {"existingSecret": "tdarr-api-key"}}


def render(overrides=None, release="tdarr"):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        values.write_text(yaml.safe_dump(overrides or {}))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", release,
             str(chart_input(CHART)), "-f", str(values)],
            text=True, capture_output=True,
        )
    return result, ([r for r in yaml.safe_load_all(result.stdout) if r]
                    if result.returncode == 0 else [])


def workloads(resources):
    return {r["metadata"]["name"]: r for r in resources if r["kind"] == "Deployment"}


def container(workload, name):
    return next(c for c in workload["spec"]["template"]["spec"]["containers"]
                if c["name"] == name)


class TdarrTests(unittest.TestCase):
    def test_default_install_ships_only_the_server(self):
        result, resources = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(workloads(resources)), ["tdarr"])
        server = container(workloads(resources)["tdarr"], "tdarr")
        self.assertEqual([m["mountPath"] for m in server["volumeMounts"]
                          if m["name"] == "config"], ["/app/server"])

    def test_scratch_is_capped_node_local_and_not_the_shared_export(self):
        # A transcode writes at full output bitrate and regenerates whatever it
        # loses, so the segments must not cross the NFS export.
        result, resources = render({**AUTH, "node": {"enabled": True, "replicaCount": 2}})
        self.assertEqual(result.returncode, 0, result.stderr)
        pods = [w["spec"]["template"]["spec"] for w in workloads(resources).values()]
        self.assertEqual(len(pods), 2)
        for pod in pods:
            scratch = next(v for v in pod["volumes"] if v["name"] == "scratch")
            self.assertIn("emptyDir", scratch)
            self.assertTrue(scratch["emptyDir"].get("sizeLimit"))
            self.assertNotIn("nfs", scratch)
            self.assertIsNone(scratch.get("persistentVolumeClaim"))

    def test_uncapped_node_local_scratch_is_refused(self):
        result, _ = render({"storage": {"scratch": {"backend": "node", "sizeLimit": None}}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sizeLimit", result.stderr)

    def test_nodes_dial_the_server_and_register_distinctly(self):
        result, resources = render({**AUTH, "node": {"enabled": True, "replicaCount": 3}})
        self.assertEqual(result.returncode, 0, result.stderr)
        node = next(w for name, w in workloads(resources).items() if name != "tdarr")
        self.assertEqual(node["spec"]["replicas"], 3)
        env = {e["name"]: e for e in container(node, "tdarr-node")["env"]}
        self.assertIn("tdarr.tdarr.svc.cluster.local", env["serverURL"]["value"])
        self.assertIn("8266", env["serverURL"]["value"])
        # A fixed name across replicas would have them overwrite each other.
        self.assertEqual(env["nodeName"]["valueFrom"]["fieldRef"]["fieldPath"],
                         "metadata.name")

    def test_a_fixed_node_name_is_refused_for_several_replicas(self):
        result, _ = render({**AUTH, "node": {"enabled": True, "replicaCount": 2,
                                             "name": "worker"}})
        self.assertNotEqual(result.returncode, 0)

    def test_nodes_without_an_api_key_are_refused(self):
        result, _ = render({"node": {"enabled": True}})
        self.assertNotEqual(result.returncode, 0)

    def test_gpu_is_opt_in_and_reaches_both_workloads(self):
        for provider, resource, extra in (
                ("intel", "gpu.intel.com/i915", {}),
                ("nvidia", "nvidia.com/gpu", {}),
                ("amd", "example.com/shared-drm", {"resourceName": "example.com/shared-drm"})):
            with self.subTest(provider=provider):
                result, resources = render({
                    **AUTH,
                    "node": {"enabled": True},
                    "gpu": {"enabled": True, "provider": provider, **extra},
                })
                self.assertEqual(result.returncode, 0, result.stderr)
                for name, workload in workloads(resources).items():
                    app = "tdarr" if name == "tdarr" else "tdarr-node"
                    limits = container(workload, app)["resources"]
                    for side in ("requests", "limits"):
                        self.assertEqual(limits[side][resource], 1, (name, side))

    def test_defaults_allocate_no_devices(self):
        result, resources = render({**AUTH, "node": {"enabled": True}})
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, workload in workloads(resources).items():
            app = "tdarr" if name == "tdarr" else "tdarr-node"
            resources_block = container(workload, app)["resources"]
            for side in ("requests", "limits"):
                self.assertEqual(set(resources_block[side]), {"cpu", "memory"}, name)
            self.assertNotIn("runtimeClassName", workload["spec"]["template"]["spec"])


if __name__ == "__main__":
    unittest.main()
