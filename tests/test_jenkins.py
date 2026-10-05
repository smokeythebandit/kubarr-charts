"""Jenkins controller render and isolation checks."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "development/jenkins"


def render(overrides=None, release="jenkins"):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        values.write_text(yaml.safe_dump(overrides or {}))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", release,
             str(chart_input(CHART)), "-f", str(values)],
            capture_output=True, text=True,
        )
    return result, ([r for r in yaml.safe_load_all(result.stdout) if r]
                    if result.returncode == 0 else [])


def one(resources, kind):
    matches = [r for r in resources if r["kind"] == kind]
    assert len(matches) == 1, (kind, len(matches))
    return matches[0]


class JenkinsTests(unittest.TestCase):
    def test_default_controller_storage_security_network_and_probes(self):
        result, resources = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.assertEqual(chart["annotations"]["kubarr.io/category"], "development")
        self.assertNotIn("kubarr.io/system", chart["annotations"])
        self.assertEqual(chart["annotations"]["kubarr.io/optional"], "true")
        self.assertEqual({r["kind"] for r in resources},
                         {"Deployment", "Service", "PersistentVolumeClaim", "PersistentVolume", "NetworkPolicy"})
        deployment = one(resources, "Deployment")
        service = one(resources, "Service")
        claim = one(resources, "PersistentVolumeClaim")
        policy = one(resources, "NetworkPolicy")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
        self.assertEqual(service["metadata"]["name"], "jenkins")
        self.assertEqual(service["spec"]["selector"], deployment["spec"]["selector"]["matchLabels"])
        self.assertEqual(service["metadata"]["annotations"]["kubarr.io/base-path"], "/jenkins")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(service["spec"]["ports"],
                         [{"port": 8080, "targetPort": "http", "protocol": "TCP", "name": "http"}])
        self.assertEqual(container["ports"], [{"name": "http", "containerPort": 8080, "protocol": "TCP"}])
        self.assertEqual(container["image"], "jenkins/jenkins:2.568.3-jdk21")
        self.assertEqual({e["name"]: e["value"] for e in container["env"]},
                         {"JENKINS_OPTS": "--prefix=/jenkins", "TZ": "Etc/UTC"})
        self.assertEqual(pod["securityContext"]["runAsUser"], 1000)
        self.assertEqual(pod["securityContext"]["runAsGroup"], 1000)
        self.assertEqual(pod["securityContext"]["fsGroup"], 1000)
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertFalse(any("hostPath" in v or "emptyDir" in v for v in pod["volumes"]))
        self.assertEqual(container["volumeMounts"], [{"name": "home", "mountPath": "/var/jenkins_home", "subPath": "development/jenkins/jenkins/home"}])
        self.assertEqual(pod["volumes"], [{"name": "home", "persistentVolumeClaim": {"claimName": claim["metadata"]["name"]}}])
        self.assertEqual(pod["initContainers"][0]["securityContext"]["capabilities"]["add"], ["CHOWN"])
        self.assertEqual(claim["spec"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(claim["spec"]["storageClassName"], "")
        self.assertEqual(one(resources, "PersistentVolume")["spec"]["nfs"]["path"], "/")
        self.assertEqual(container["resources"]["limits"], {"cpu": "2", "memory": "4Gi"})
        for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
            self.assertEqual(container[probe]["httpGet"], {"path": "/jenkins/login", "port": "http"})
        self.assertGreaterEqual(container["startupProbe"]["failureThreshold"] *
                                container["startupProbe"]["periodSeconds"], 600)
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], service["spec"]["selector"])
        self.assertEqual(policy["spec"]["ingress"], [{"from": [{"namespaceSelector": {"matchLabels":
            {"kubernetes.io/metadata.name": "openresty"}}}],
            "ports": [{"protocol": "TCP", "port": 8080}]}])
        self.assertEqual(policy["spec"]["egress"][1]["to"][0]["ipBlock"]["cidr"], "0.0.0.0/0")
        self.assertIn("10.0.0.0/8", policy["spec"]["egress"][1]["to"][0]["ipBlock"]["except"])

    def test_existing_claim_custom_release_and_private_git_namespace(self):
        result, resources = render({
            "storage": {"home": {"existingClaim": "ci-home"}},
            "jenkins": {"service": {"port": 8181}, "env": {"TZ": "Europe/Paris"}},
            "networkPolicy": {"egressTo": ["git-services"]},
        }, release="ci")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse([r for r in resources if r["kind"] == "PersistentVolumeClaim"])
        deployment = one(resources, "Deployment")
        self.assertEqual(deployment["metadata"]["name"], "ci-jenkins")
        self.assertEqual(deployment["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"], "ci-home")
        self.assertEqual(one(resources, "Service")["spec"]["ports"][0]["port"], 8181)
        self.assertEqual(one(resources, "NetworkPolicy")["spec"]["ingress"][0]["ports"][0]["port"], 8080)
        self.assertEqual(one(resources, "NetworkPolicy")["spec"]["egress"][2]["to"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "git-services"})

    def test_unsafe_or_broken_routing_values_fail(self):
        for overrides, message in [
            ({"jenkins": {"basePath": "/other"}}, "jenkins.basePath"),
            ({"jenkins": {"service": {"targetPort": 50000}}}, "jenkins.service.targetPort"),
            ({"jenkins": {"service": {"type": "NodePort"}}}, "jenkins.service.type"),
            ({"storage": {"home": {"subPath": "../other"}}}, "safe relative"),
            ({"storage": {"home": {"existingClaim": "media-data"}}}, "shared media"),
            ({"jenkins": {"env": {"JENKINS_OPTS": "--prefix=/other"}}}, "jenkins.env"),
        ]:
            with self.subTest(message=message):
                result, _ = render(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_explicit_dedicated_class_bypasses_managed_nfs(self):
        result, resources = render({"storage": {"home": {"storageClassName": "nfs-client"}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse([r for r in resources if r["kind"] == "PersistentVolume"])
        self.assertEqual(one(resources, "PersistentVolumeClaim")["spec"]["storageClassName"], "nfs-client")
        self.assertEqual(one(resources, "Deployment")["spec"]["template"]["spec"]["containers"][0]["volumeMounts"],
                         [{"name": "home", "mountPath": "/var/jenkins_home"}])


if __name__ == "__main__":
    unittest.main()
