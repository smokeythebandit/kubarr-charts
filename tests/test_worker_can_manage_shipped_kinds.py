"""Helm reads every resource it owns before upgrading a release.

A chart that ships a resource kind the worker's ClusterRole does not cover
produces an upgrade that fails on its own manifest, with an error naming a
resource the user never touched. That happened with CiliumNetworkPolicy: the
monitoring charts shipped them, the worker could not read them, and every
update of those apps failed.
"""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CILIUM_API_VERSION = "cilium.io/v2"
# Kinds a chart may ship that the worker must therefore be able to read.
GUARDED = {
    "CiliumNetworkPolicy": ("cilium.io", "ciliumnetworkpolicies"),
    "CiliumClusterwideNetworkPolicy": ("cilium.io", "ciliumclusterwidenetworkpolicies"),
    "NetworkPolicy": ("networking.k8s.io", "networkpolicies"),
}


def charts():
    return sorted(p.parent for p in ROOT.glob("*/*/Chart.yaml"))


def render(chart):
    command = [os.environ.get("HELM", "helm"), "template", chart.name, str(chart_input(chart))]
    command += ["--api-versions", CILIUM_API_VERSION]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        return []
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def worker_rules():
    rules = []
    for doc in render(ROOT / "system/kubarr-worker"):
        if doc.get("kind") == "ClusterRole":
            rules.extend(doc.get("rules", []))
    return rules


class WorkerCanManageEveryShippedKind(unittest.TestCase):
    def test_shipped_kinds_are_covered_by_worker_rbac(self):
        rules = worker_rules()
        self.assertTrue(rules, "worker ClusterRole must render")

        def permitted(group, resource):
            for rule in rules:
                groups = rule.get("apiGroups", [])
                resources = rule.get("resources", [])
                verbs = rule.get("verbs", [])
                if (group in groups or "*" in groups) and (
                    resource in resources or "*" in resources
                ):
                    if "get" in verbs or "*" in verbs:
                        return True
            return False

        for chart in charts():
            for doc in render(chart):
                kind = doc.get("kind")
                if kind not in GUARDED:
                    continue
                group, resource = GUARDED[kind]
                with self.subTest(chart=chart.relative_to(ROOT), kind=kind):
                    self.assertTrue(
                        permitted(group, resource),
                        f"{chart.name} ships {kind}, so the worker needs get on "
                        f"{resource}.{group} or its Helm upgrades fail",
                    )


if __name__ == "__main__":
    unittest.main()
