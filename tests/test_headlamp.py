"""Render the optional Headlamp chart and check gateway and login boundaries."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

from chart_artifacts import chart_input


CHART = Path(__file__).resolve().parents[1] / "monitoring/headlamp"


def template(*overrides, release="headlamp"):
    cmd = [os.environ.get("HELM", "helm"), "template", release, str(chart_input(CHART))]
    for override in overrides:
        cmd.extend(["--set", override])
    return subprocess.run(cmd, capture_output=True, text=True)


def render(*overrides, release="headlamp"):
    result = template(*overrides, release=release)
    result.check_returncode()
    return {item["kind"]: item for item in yaml.safe_load_all(result.stdout) if item}


class HeadlampTests(unittest.TestCase):
    def test_default_routes_login_and_runtime(self):
        resources = render()
        self.assertEqual(set(resources), {"ServiceAccount", "Service", "Deployment", "NetworkPolicy"})
        service, deploy = resources["Service"], resources["Deployment"]
        pod = deploy["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(service["metadata"]["annotations"]["kubarr.io/base-path"], "/headlamp")
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], "http")
        self.assertEqual(container["ports"][0]["containerPort"], 4466)
        self.assertEqual(container["image"], "ghcr.io/headlamp-k8s/headlamp:v0.45.0")
        self.assertIn("-in-cluster", container["args"])
        self.assertIn("-base-url=/headlamp", container["args"])
        self.assertFalse(any("unsafe" in arg or "skip-login" in arg for arg in container["args"]))
        self.assertTrue(pod["automountServiceAccountToken"])
        self.assertEqual(pod["serviceAccountName"], resources["ServiceAccount"]["metadata"]["name"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(container["volumeMounts"], [{"name": "tmp", "mountPath": "/tmp"}])
        self.assertEqual(pod["volumes"], [{"name": "tmp", "emptyDir": {}}])
        for probe in ("livenessProbe", "readinessProbe"):
            self.assertEqual(container[probe]["httpGet"],
                             {"path": "/headlamp/", "port": "http", "scheme": "HTTP"})
        policy = resources["NetworkPolicy"]["spec"]
        self.assertEqual(policy["podSelector"]["matchLabels"], deploy["spec"]["selector"]["matchLabels"])
        self.assertEqual(policy["ingress"][0]["ports"][0]["port"], 4466)
        self.assertEqual(policy["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "openresty"})
        self.assertEqual([port["port"] for port in policy["egress"][1]["ports"]], [443, 6443])

    def test_release_and_policy_overrides(self):
        resources = render("fullnameOverride=custom-headlamp", "headlamp.service.port=8080",
                           "networkPolicy.apiServerCIDRs[0]=10.43.0.1/32",
                           "networkPolicy.apiServerPorts[0]=7443", release="my-release")
        deploy = resources["Deployment"]
        self.assertEqual(deploy["metadata"]["name"], "custom-headlamp")
        self.assertEqual(resources["Service"]["spec"]["ports"][0]["port"], 8080)
        self.assertEqual(resources["NetworkPolicy"]["spec"]["podSelector"]["matchLabels"],
                         deploy["spec"]["template"]["metadata"]["labels"])
        self.assertEqual(resources["NetworkPolicy"]["spec"]["egress"][1]["to"][0]["ipBlock"]["cidr"],
                         "10.43.0.1/32")
        self.assertEqual(resources["NetworkPolicy"]["spec"]["egress"][1]["ports"][0]["port"], 7443)
        self.assertNotIn("NetworkPolicy", render("networkPolicy.enabled=false"))

    def test_rejects_broken_gateway_or_port(self):
        for override in ("headlamp.basePath=/", "headlamp.service.targetPort=8080"):
            with self.subTest(override=override):
                self.assertNotEqual(template(override).returncode, 0)


if __name__ == "__main__":
    unittest.main()
