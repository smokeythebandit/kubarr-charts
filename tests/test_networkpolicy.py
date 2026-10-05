"""Render the shipped charts and verify shared NetworkPolicies select their pods."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

from chart_artifacts import chart_input


ROOT = Path(__file__).resolve().parents[1]
CHARTS = sorted(
    template.parent.parent
    for template in ROOT.glob("*/*/templates/networkpolicy.yaml")
    if '"kubarr-common.networkPolicy"' in template.read_text()
)
# These images listen on port 8080; their charts reject a different targetPort.
FIXED_HTTP_PORTS = {"jenkins": 8080, "scm-manager": 8080}


def template(chart, release, *values):
    command = [os.environ.get("HELM", "helm"), "template", release, str(chart_input(chart))]
    for value in values:
        command.extend(["--set", value])
    return subprocess.run(command, capture_output=True, text=True)


def render(chart, release, *values):
    result = template(chart, release, *values)
    result.check_returncode()
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


class NetworkPolicyTests(unittest.TestCase):
    def test_selectors_and_ingress_ports(self):
        self.assertTrue(CHARTS, "No shared NetworkPolicy consumers found")
        for chart in CHARTS:
            cases = [
                (chart.name, ()),
                ("custom-release", ()),
                ("custom-release", ("nameOverride=custom-app",)),
                ("custom-release", ("nameOverride=" + "long-name-" * 8,)),
                ("custom-release", ("fullnameOverride=custom-workload",)),
                (
                    "custom-release",
                    (
                        f"{chart.name}.service.port=18080",
                        f"{chart.name}.service.targetPort={FIXED_HTTP_PORTS.get(chart.name, 18081)}",
                    ),
                ),
            ]
            for release, values in cases:
                with self.subTest(chart=chart.name, release=release, values=values):
                    resources = render(chart, release, *values)
                    policies = [r for r in resources if r["kind"] == "NetworkPolicy"]
                    deployments = [
                        r for r in resources
                        if r["kind"] == "Deployment"
                        and any(
                            c["name"] == chart.name
                            for c in r["spec"]["template"]["spec"]["containers"]
                        )
                    ]
                    self.assertEqual(len(policies), 1)
                    self.assertEqual(len(deployments), 1)
                    policy, deployment = policies[0], deployments[0]
                    pod = deployment["spec"]["template"]
                    services = [
                        r for r in resources
                        if r["kind"] == "Service"
                        and r["spec"].get("selector") == deployment["spec"]["selector"]["matchLabels"]
                        and any(p.get("name") == "http" for p in r["spec"].get("ports", []))
                    ]
                    self.assertEqual(len(services), 1)
                    selector = policy["spec"]["podSelector"]["matchLabels"]
                    self.assertEqual(selector, deployment["spec"]["selector"]["matchLabels"])
                    self.assertEqual(selector["app.kubernetes.io/instance"], release)
                    self.assertEqual(
                        policy["metadata"]["namespace"], deployment["metadata"]["namespace"]
                    )
                    for key, value in selector.items():
                        self.assertEqual(pod["metadata"]["labels"][key], value)

                    ports = {
                        port["name"]: port["containerPort"]
                        for container in pod["spec"]["containers"]
                        for port in container.get("ports", [])
                    }
                    http_service_port = next(p for p in services[0]["spec"]["ports"] if p["name"] == "http")
                    self.assertEqual(ports.get(http_service_port["targetPort"], http_service_port["targetPort"]), ports["http"])
                    if chart.name in FIXED_HTTP_PORTS:
                        self.assertEqual(ports["http"], FIXED_HTTP_PORTS[chart.name])
                        if values and values[0] == f"{chart.name}.service.port=18080":
                            self.assertEqual(http_service_port["port"], 18080)
                    self.assertEqual(set(policy["spec"]["policyTypes"]), {"Ingress", "Egress"})
                    self.assertTrue(policy["spec"]["ingress"])
                    for rule in policy["spec"]["ingress"]:
                        allowed_ports = {
                            ports.get(port["port"], port["port"])
                            for port in rule["ports"]
                        }
                        self.assertIn(ports["http"], allowed_ports)

    def test_fixed_http_ports_reject_unsupported_override(self):
        for chart in CHARTS:
            if chart.name not in FIXED_HTTP_PORTS:
                continue
            with self.subTest(chart=chart.name):
                result = template(chart, "custom-release", f"{chart.name}.service.targetPort=18081")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{chart.name}.service.targetPort", result.stderr)

    def test_disabled(self):
        for chart in CHARTS:
            with self.subTest(chart=chart.name):
                resources = render(chart, "custom-release", "networkPolicy.enabled=false")
                self.assertFalse(any(r["kind"] == "NetworkPolicy" for r in resources))


if __name__ == "__main__":
    unittest.main()
