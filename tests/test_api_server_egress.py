"""Workloads that call the Kubernetes API keep reaching it under policy.

A NetworkPolicy egress rule to an ipBlock cannot express "the API server":
the apiserver runs in the host network, and Cilium identifies that
destination as the host, which CIDR selectors never match. Those charts
therefore ship a CiliumNetworkPolicy naming the kube-apiserver entity, but
only on clusters that have the CRD for it.
"""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


# Charts whose Pods call the API and whose own policy restricts egress.
NEEDS_API = ("monitoring/victoriametrics", "monitoring/fluent-bit", "monitoring/headlamp")
CILIUM_API_VERSION = "cilium.io/v2"


def render(chart, cilium):
    command = [os.environ.get("HELM", "helm"), "template", chart.name, str(chart_input(chart))]
    if cilium:
        command += ["--api-versions", CILIUM_API_VERSION]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def cilium_policies(resources):
    return [d for d in resources if d.get("kind") == "CiliumNetworkPolicy"]


class ApiServerEgressTests(unittest.TestCase):
    def test_api_callers_allow_the_apiserver_entity_on_cilium(self):
        for chart in NEEDS_API:
            with self.subTest(chart=chart):
                policies = cilium_policies(render(ROOT / chart, cilium=True))
                self.assertEqual(len(policies), 1, "expected one CiliumNetworkPolicy")
                egress = policies[0]["spec"]["egress"]
                entities = [e for rule in egress for e in rule.get("toEntities", [])]
                self.assertIn("kube-apiserver", entities)

    def test_nothing_cilium_specific_renders_without_the_crd(self):
        # A cluster without Cilium must not receive a resource whose kind does
        # not exist there; the install would fail outright.
        for chart in NEEDS_API:
            with self.subTest(chart=chart):
                self.assertEqual(cilium_policies(render(ROOT / chart, cilium=False)), [])

    def test_charts_without_api_work_ship_no_apiserver_policy(self):
        for chart in ("media-manager/sonarr", "download-client/qbittorrent",
                      "media-server/jellyfin"):
            with self.subTest(chart=chart):
                self.assertEqual(cilium_policies(render(ROOT / chart, cilium=True)), [])


if __name__ == "__main__":
    unittest.main()
