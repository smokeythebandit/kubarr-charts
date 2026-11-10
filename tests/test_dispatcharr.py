"""Dispatcharr: the IPTV manager Jellyfin's Live TV reads from."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = "media-server/dispatcharr"


def render(*sets, check=True):
    command = [os.environ.get("HELM", "helm"), "template", "dispatcharr", str(chart_input(ROOT / CHART))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result if not check else [d for d in yaml.safe_load_all(result.stdout) if d]


def kind(resources, name):
    return next(d for d in resources if d["kind"] == name)


class DispatcharrTests(unittest.TestCase):
    def test_all_in_one_on_a_private_claim(self):
        resources = render()
        pod = kind(resources, "Deployment")["spec"]["template"]["spec"]
        app = pod["containers"][0]
        env = {e["name"]: e["value"] for e in app["env"]}
        self.assertEqual(env["DISPATCHARR_ENV"], "aio")
        self.assertEqual(env["DISPATCHARR_PORT"], "9191")
        self.assertEqual(app["volumeMounts"], [{"name": "config", "mountPath": "/data"}])
        claim = kind(resources, "PersistentVolumeClaim")
        self.assertEqual(claim["spec"]["accessModes"], ["ReadWriteOnce"])
        # The embedded PostgreSQL never lands on the shared NFS export.
        self.assertFalse([v for v in pod["volumes"] if v["name"] == "media"])
        self.assertEqual(kind(resources, "Deployment")["spec"]["strategy"]["type"], "Recreate")

    def test_capabilities_stay_within_baseline(self):
        context = kind(render(), "Deployment")["spec"]["template"]["spec"]["containers"][0]["securityContext"]
        baseline = {"AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD",
                    "NET_BIND_SERVICE", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"}
        self.assertEqual(context["capabilities"]["drop"], ["ALL"])
        self.assertTrue(set(context["capabilities"]["add"]) <= baseline)
        self.assertFalse(context["allowPrivilegeEscalation"])

    def test_only_the_gateway_and_jellyfin_connect(self):
        policy = kind(render(), "NetworkPolicy")["spec"]
        sources = {peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                   for rule in policy["ingress"] for peer in rule["from"]}
        self.assertEqual(sources, {"openresty", "jellyfin"})

    def test_catalog_entry(self):
        annotations = yaml.safe_load((ROOT / CHART / "Chart.yaml").read_text())["annotations"]
        self.assertEqual(annotations["kubarr.io/category"], "media-server")
        self.assertEqual(annotations["kubarr.io/browseable"], "true")


if __name__ == "__main__":
    unittest.main()
