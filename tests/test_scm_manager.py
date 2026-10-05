"""SCM-Manager persistence, gateway and bootstrap render checks."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "development/scm-manager"


def render(overrides=None, release="scm-manager"):
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


def resource(resources, kind):
    return next(r for r in resources if r["kind"] == kind)


class ScmManagerTests(unittest.TestCase):
    def test_defaults(self):
        result, resources = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.assertEqual(chart["annotations"]["kubarr.io/category"], "development")
        deployment = resource(resources, "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        app = pod["containers"][0]
        service = resource(resources, "Service")
        claim = resource(resources, "PersistentVolumeClaim")
        policy = resource(resources, "NetworkPolicy")["spec"]
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
        self.assertEqual(app["image"], "scmmanager/scm-manager:3.12.1")
        self.assertEqual(app["ports"][0]["containerPort"], 8080)
        self.assertEqual(service["metadata"]["annotations"]["kubarr.io/base-path"], "/scm-manager")
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], "http")
        self.assertEqual(service["spec"]["selector"], deployment["spec"]["selector"]["matchLabels"])
        self.assertEqual(policy["podSelector"]["matchLabels"], service["spec"]["selector"])
        self.assertEqual(policy["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "openresty"})
        self.assertEqual(policy["ingress"][0]["ports"][0]["port"], 8080)
        self.assertEqual([p["port"] for p in service["spec"]["ports"]], [8080])
        self.assertEqual(claim["spec"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(claim["spec"]["storageClassName"], "")
        self.assertEqual(resource(resources, "PersistentVolume")["spec"]["nfs"]["path"], "/")
        self.assertEqual(pod["volumes"][0]["persistentVolumeClaim"]["claimName"], claim["metadata"]["name"])
        self.assertEqual(app["volumeMounts"], [{"name": "home", "mountPath": "/var/lib/scm", "subPath": "development/scm-manager/scm-manager/home"}])
        self.assertEqual(pod["initContainers"][0]["securityContext"]["capabilities"]["add"], ["CHOWN"])
        self.assertEqual(pod["securityContext"]["runAsUser"], 1000)
        self.assertEqual(pod["securityContext"]["fsGroup"], 1000)
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(app["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(app["securityContext"]["capabilities"]["drop"], ["ALL"])
        env = {e["name"]: e for e in app["env"]}
        self.assertEqual(env["SCM_CONTEXT_PATH"]["value"], "/scm-manager")
        self.assertEqual(env["SCM_FORWARD_HEADERS_ENABLED"]["value"], "true")
        self.assertNotIn("SCM_WEBAPP_INITIALPASSWORD", env)
        self.assertFalse(any(r["kind"] == "Secret" for r in resources))
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            self.assertEqual(app[probe]["httpGet"]["path"], "/scm-manager/")

    def test_existing_claim_secret_and_policy_overrides(self):
        result, resources = render({
            "storage": {"home": {"existingClaim": "git-data"}},
            "scm-manager": {"bootstrap": {"existingSecret": "bootstrap", "passwordKey": "password",
                                            "initialUser": "admin"},
                            "service": {"port": 9090}},
            "networkPolicy": {"ingressFrom": ["gateway"], "egressTo": ["plugins"]},
        }, "git")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(r["kind"] in ("PersistentVolumeClaim", "Secret") for r in resources))
        pod = resource(resources, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(pod["volumes"][0]["persistentVolumeClaim"]["claimName"], "git-data")
        env = {e["name"]: e for e in pod["containers"][0]["env"]}
        self.assertEqual(env["SCM_WEBAPP_INITIALPASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "bootstrap", "key": "password"})
        self.assertEqual(env["SCM_WEBAPP_INITIALUSER"]["value"], "admin")
        self.assertEqual(resource(resources, "Service")["spec"]["ports"][0]["port"], 9090)
        policy = resource(resources, "NetworkPolicy")["spec"]
        self.assertEqual(policy["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "gateway"})
        self.assertEqual(policy["ingress"][0]["ports"][0]["port"], 8080)
        self.assertEqual(policy["egress"][-1]["to"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "plugins"})

    def test_custom_storage_class_and_long_name(self):
        result, resources = render({"storage": {"home": {"storageClassName": "fast-rwo", "size": "100Gi"}},
                                    "fullnameOverride": "git-" * 18})
        self.assertEqual(result.returncode, 0, result.stderr)
        claim = resource(resources, "PersistentVolumeClaim")
        self.assertEqual(claim["spec"]["storageClassName"], "fast-rwo")
        self.assertEqual(claim["spec"]["resources"]["requests"]["storage"], "100Gi")
        self.assertTrue(all(len(r["metadata"]["name"]) <= 63 for r in resources))
        self.assertEqual(resource(resources, "Service")["spec"]["selector"],
                         resource(resources, "Deployment")["spec"]["template"]["metadata"]["labels"])

    def test_policy_can_be_disabled(self):
        result, resources = render({"networkPolicy": {"enabled": False}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(r["kind"] == "NetworkPolicy" for r in resources))

    def test_invalid_single_writer_and_routing(self):
        for values, message in [
            ({"scm-manager": {"replicaCount": 2}}, "replicaCount"),
            ({"scm-manager": {"service": {"targetPort": 2222}}}, "targetPort"),
            ({"scm-manager": {"service": {"type": "NodePort"}}}, "ClusterIP"),
            ({"scm-manager": {"basePath": "/wrong"}}, "basePath"),
            ({"storage": {"home": {"subPath": "../other"}}}, "safe relative"),
            ({"scm-manager": {"bootstrap": {"existingSecret": "bootstrap", "passwordKey": ""}}}, "passwordKey"),
        ]:
            with self.subTest(values=values):
                result, _ = render(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
