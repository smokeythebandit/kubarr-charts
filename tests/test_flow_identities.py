"""LAN traffic is only separable from internet traffic if CIDR identities exist.

Cilium labels every destination outside the cluster `reserved:world`. A CIDR
identity gives the local ranges their own label, which is what lets the network
page tell LAN traffic from internet traffic. Importing a policy that references
a CIDR is what mints the identity; the policy below selects no Pod on purpose,
because selecting one would switch it to default-deny for that direction.
"""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CILIUM_API_VERSION = "cilium.io/v2"
IDENTITIES_CHART = "system/kubarr-cilium-identities"


def render(chart, cilium=True):
    command = [os.environ.get("HELM", "helm"), "template", chart.name, str(chart_input(chart))]
    if cilium:
        command += ["--api-versions", CILIUM_API_VERSION]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


class CidrIdentityPolicy(unittest.TestCase):
    def test_policy_selects_no_pod(self):
        policies = [
            d
            for d in render(ROOT / IDENTITIES_CHART)
            if d.get("kind") == "CiliumClusterwideNetworkPolicy"
        ]
        self.assertEqual(len(policies), 1)
        selector = policies[0]["spec"]["endpointSelector"]["matchLabels"]
        self.assertEqual(
            selector,
            {"kubarr.io/cidr-identity-only": "true"},
            "a selector that matches a real Pod would switch it to default-deny",
        )

    def test_policy_lists_the_lan_ranges(self):
        policy = next(
            d
            for d in render(ROOT / IDENTITIES_CHART)
            if d.get("kind") == "CiliumClusterwideNetworkPolicy"
        )
        cidrs = {
            entry["cidr"]
            for rule in policy["spec"]["egress"]
            for entry in rule["toCIDRSet"]
        }
        self.assertEqual(cidrs, {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"})

    def test_no_policy_renders_without_the_cilium_crd(self):
        # Other CNIs have no such resource, so installing it would fail. The
        # ConfigMap recording the configured ranges still renders, which is how
        # the intent stays inspectable on a cluster running another CNI.
        rendered = render(ROOT / IDENTITIES_CHART, cilium=False)
        kinds = {d.get("kind") for d in rendered}
        self.assertNotIn("CiliumClusterwideNetworkPolicy", kinds)
        self.assertIn("ConfigMap", kinds)


class VictoriaMetricsReachesTheAgents(unittest.TestCase):
    def test_kube_system_is_scrapeable(self):
        allowed = set()
        for policy in [
            d
            for d in render(ROOT / "monitoring/victoriametrics")
            if d.get("kind") == "NetworkPolicy"
        ]:
            for rule in policy["spec"].get("egress", []):
                for target in rule.get("to", []):
                    name = (
                        target.get("namespaceSelector", {})
                        .get("matchLabels", {})
                        .get("kubernetes.io/metadata.name")
                    )
                    if name:
                        allowed.add(name)
        self.assertIn(
            "kube-system",
            allowed,
            "Hubble metrics live on the Cilium agents; without this the panels are silently empty",
        )


if __name__ == "__main__":
    unittest.main()
