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
        app = next(c for c in pod["containers"] if c["name"] == "dispatcharr")
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

    def test_gateway_reaches_only_the_ui_and_jellyfin_only_the_tuner(self):
        policy = kind(render(), "NetworkPolicy")["spec"]
        rules = {rule["from"][0]["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]:
                 [p["port"] for p in rule["ports"]] for rule in policy["ingress"]}
        self.assertEqual(rules, {"openresty": [9191], "jellyfin": [9192]})

    def test_tuner_proxy_serves_only_tuner_paths_and_drops_identity(self):
        config = next(d for d in render() if d["kind"] == "ConfigMap"
                      and d["metadata"]["name"].endswith("-tuner"))["data"]["default.conf"]
        self.assertIn("location ~ ^/(hdhr|output|proxy)(/|$)", config)
        self.assertIn('proxy_set_header X-Kubarr-User "";', config)
        self.assertIn("location / {\n    return 403;", config.replace("      ", "  ").replace("        ", "    "))
        self.assertIn("listen 9192;", config)
        self.assertIn("proxy_pass http://127.0.0.1:9191;", config)
        pod = kind(render(), "Deployment")["spec"]["template"]["spec"]
        tuner = next(c for c in pod["containers"] if c["name"] == "tuner")
        self.assertTrue(tuner["securityContext"]["readOnlyRootFilesystem"])
        self.assertTrue(tuner["securityContext"]["runAsNonRoot"])
        service = kind(render(), "Service")
        self.assertEqual([(p["name"], p["port"]) for p in service["spec"]["ports"]],
                         [("http", 9191), ("tuner", 9192)])

    def test_reverse_proxy_sign_in_trusts_only_private_peers(self):
        app = next(c for c in kind(render(), "Deployment")["spec"]["template"]["spec"]["containers"]
                   if c["name"] == "dispatcharr")
        env = {e["name"]: e["value"] for e in app["env"]}
        self.assertEqual(env["DISPATCHARR_TRUSTED_PROXIES"], "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
        annotations = yaml.safe_load((ROOT / CHART / "Chart.yaml").read_text())["annotations"]
        self.assertEqual(annotations["kubarr.io/user-header"], "true")

    def test_gpu_is_offered_and_off_by_default(self):
        values = yaml.safe_load((ROOT / CHART / "values.yaml").read_text())
        self.assertIs(values["gpu"]["enabled"], False)  # the install wizard keys on gpu.enabled
        app = kind(render(), "Deployment")["spec"]["template"]["spec"]["containers"][0]
        self.assertFalse(any("gpu" in k for side in app["resources"].values() for k in side))

    def test_gpu_requests_the_device_and_nvidia_capabilities(self):
        intel = kind(render("gpu.enabled=true"), "Deployment")["spec"]["template"]["spec"]
        app = intel["containers"][0]
        self.assertEqual(app["resources"]["limits"]["gpu.intel.com/i915"], 1)
        self.assertEqual(app["resources"]["requests"]["gpu.intel.com/i915"], 1)
        nvidia = kind(render("gpu.enabled=true", "gpu.provider=nvidia", "gpu.runtimeClassName=nvidia"),
                      "Deployment")["spec"]["template"]["spec"]
        env = {e["name"]: e["value"] for e in nvidia["containers"][0]["env"]}
        self.assertEqual(env["NVIDIA_DRIVER_CAPABILITIES"], "video,utility")
        self.assertEqual(nvidia["runtimeClassName"], "nvidia")
        self.assertEqual(nvidia["containers"][0]["resources"]["limits"]["nvidia.com/gpu"], 1)

    def test_catalog_entry(self):
        annotations = yaml.safe_load((ROOT / CHART / "Chart.yaml").read_text())["annotations"]
        self.assertEqual(annotations["kubarr.io/category"], "media-server")
        self.assertEqual(annotations["kubarr.io/browseable"], "true")


if __name__ == "__main__":
    unittest.main()
