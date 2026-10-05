"""Frigate proxy, persistent state, isolation and opt-in render checks."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "media-server/frigate"


def render(overrides=None, release="frigate"):
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


def objects(resources, kind):
    return {r["metadata"]["name"]: r for r in resources if r["kind"] == kind}


class FrigateTests(unittest.TestCase):
    def test_default_seed_proxy_and_local_database(self):
        result, resources = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.assertEqual(chart["appVersion"], "0.18.0")
        self.assertEqual(chart["annotations"]["kubarr.io/category"], "media-server")
        deployment = objects(resources, "Deployment")["frigate"]
        pod = deployment["spec"]["template"]["spec"]
        app = pod["containers"][0]
        self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
        self.assertEqual(app["image"], "ghcr.io/blakeblackshear/frigate:0.18.0")
        self.assertEqual(objects(resources, "Service")["frigate"]["spec"]["ports"],
                         [{"port": 8971, "targetPort": "http", "protocol": "TCP", "name": "http"}])
        self.assertEqual(app["ports"], [{"name": "http", "containerPort": 8971, "protocol": "TCP"}])
        self.assertEqual(objects(resources, "NetworkPolicy")["frigate"]["spec"]["ingress"][0]["ports"],
                          [{"protocol": "TCP", "port": 8971}])
        self.assertEqual(objects(resources, "NetworkPolicy")["frigate"]["spec"]["podSelector"]["matchLabels"],
                         deployment["spec"]["selector"]["matchLabels"])
        self.assertEqual(pod["automountServiceAccountToken"], False)
        self.assertFalse(app["securityContext"].get("privileged", False))
        self.assertFalse(any("hostPath" in v for v in pod["volumes"]))
        self.assertNotIn("runtimeClassName", pod)
        self.assertNotIn("nvidia.com/gpu", str(app["resources"]))
        claims = objects(resources, "PersistentVolumeClaim")
        self.assertNotIn("storageClassName", claims["frigate-config"]["spec"])
        self.assertEqual(claims["media-data"]["spec"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(objects(resources, "PersistentVolumeClaim")["media-data"]["spec"]["storageClassName"], "")
        self.assertEqual(len(objects(resources, "PersistentVolume")), 1)
        volumes = {v["name"]: v for v in pod["volumes"]}
        self.assertEqual(volumes["config"]["persistentVolumeClaim"]["claimName"], "frigate-config")
        self.assertEqual(volumes["media"]["persistentVolumeClaim"]["claimName"], "media-data")
        self.assertEqual(volumes["shm"]["emptyDir"], {"medium": "Memory", "sizeLimit": "128Mi"})
        self.assertEqual(volumes["cache"]["emptyDir"]["medium"], "Memory")
        mounts = {v["name"]: v for v in app["volumeMounts"]}
        self.assertEqual(mounts["config"]["mountPath"], "/config")
        self.assertEqual(mounts["media"]["mountPath"], "/media/frigate")
        self.assertEqual(mounts["media"]["subPath"], "frigate")
        self.assertEqual(mounts["shm"]["mountPath"], "/dev/shm")
        self.assertEqual(mounts["cache"]["mountPath"], "/tmp/cache")
        seed = yaml.safe_load(objects(resources, "ConfigMap")["frigate-seed"]["data"]["config.yml"])
        self.assertEqual(seed["mqtt"]["enabled"], False)
        self.assertEqual(seed["tls"]["enabled"], False)
        self.assertEqual(seed["cameras"]["dummy_camera"]["enabled"], False)
        self.assertIn("[ ! -e /config/config.yml ] && [ ! -e /config/config.yaml ]",
                      pod["initContainers"][0]["command"][2])
        for probe in ("startupProbe", "livenessProbe", "readinessProbe"):
            self.assertEqual(app[probe]["httpGet"], {"path": "/", "port": "http"})

    def test_custom_claim_secrets_private_egress_and_gpu(self):
        override = {
            "storage": {"config": {"existingClaim": "local-db"},
                        "media": {"create": False, "existingClaim": "surveillance", "subPath": "nvr"},
                        "shmSize": "256Mi"},
            "frigate": {"existingSecret": "frigate-credentials"},
            "gpu": {"enabled": True, "provider": "nvidia", "runtimeClassName": "nvidia"},
            "networkPolicy": {"privateEgressCIDRs": ["192.168.4.0/24"]},
        }
        result, resources = render(override, "nvr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "PersistentVolumeClaim"))
        pod = objects(resources, "Deployment")["nvr-frigate"]["spec"]["template"]["spec"]
        app = pod["containers"][0]
        self.assertEqual(pod["runtimeClassName"], "nvidia")
        self.assertEqual(app["resources"]["limits"]["nvidia.com/gpu"], 1)
        self.assertEqual(app["envFrom"], [{"secretRef": {"name": "frigate-credentials"}}])
        self.assertEqual(pod["volumes"][0]["persistentVolumeClaim"]["claimName"], "local-db")
        self.assertEqual(pod["volumes"][1]["persistentVolumeClaim"]["claimName"], "surveillance")
        self.assertEqual(objects(resources, "NetworkPolicy")["nvr-frigate-camera-egress"]["spec"]["egress"],
                          [{"to": [{"ipBlock": {"cidr": "192.168.4.0/24"}}]}])
        ingress = objects(resources, "NetworkPolicy")["nvr"]["spec"]["ingress"]
        self.assertEqual(ingress[0]["ports"], [{"protocol": "TCP", "port": 8971}])
        self.assertEqual(ingress[0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "openresty"})

    def test_unsafe_options_fail_render(self):
        for values, error in (
            ({"frigate": {"replicaCount": 2}}, "replicaCount"),
            ({"frigate": {"service": {"targetPort": 5000}}}, "8971"),
            ({"frigate": {"service": {"type": "NodePort"}}}, "ClusterIP"),
            ({"storage": {"config": {"storageClassName": "nfs-client"}}}, "local block"),
            ({"storage": {"config": {"existingClaim": "media-data"}}}, "local block"),
            ({"networkPolicy": {"enabled": False}}, "networkPolicy"),
            ({"storage": {"media": {"existingClaim": ""}}}, "storage.media.existingClaim"),
        ):
            with self.subTest(error=error):
                result, _ = render(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)

    def test_long_fullname_keeps_camera_policy_name_valid(self):
        result, resources = render({"fullnameOverride": "nvr-" * 17,
                                    "networkPolicy": {"privateEgressCIDRs": ["10.1.0.0/24"]}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(len(r["metadata"]["name"]) <= 63 for r in resources))


if __name__ == "__main__":
    unittest.main()
