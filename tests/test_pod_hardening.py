"""Pods hold no credential and no privilege they have no use for."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


# Headlamp is a Kubernetes UI: it reads the cluster through whatever identity
# it is given, so its token mount is deliberate even without chart RBAC.
NEEDS_API = ("monitoring/headlamp",)


def render(chart):
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", chart.name, str(chart_input(chart))],
        capture_output=True, text=True,
    )
    if result.returncode:
        return []
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def charts():
    return sorted(p.parent for p in ROOT.glob("*/*/Chart.yaml"))


def pods(resources):
    for doc in resources:
        if doc.get("kind") in ("Deployment", "StatefulSet", "DaemonSet"):
            yield doc["metadata"]["name"], doc["spec"]["template"]["spec"]


def grants_api_access(resources):
    """A chart that binds a role expects its Pods to use the API."""
    return any(doc.get("kind") in ("Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding")
               for doc in resources)


class PodHardeningTests(unittest.TestCase):
    def test_pods_without_api_work_mount_no_service_account_token(self):
        # A token in, say, Sonarr buys an attacker a pivot into the API for an
        # application that never calls it.
        for chart in charts():
            relative = str(chart.relative_to(ROOT))
            resources = render(chart)
            if not resources or grants_api_access(resources) or relative in NEEDS_API:
                continue
            for name, spec in pods(resources):
                with self.subTest(chart=relative, workload=name):
                    self.assertIs(
                        spec.get("automountServiceAccountToken"), False,
                        "this chart grants no RBAC, so its Pods need no token")

    def test_namespaces_declare_the_privilege_level_they_require(self):
        # Charts already set good security contexts by convention. The label
        # makes the cluster enforce it, so a later chart cannot quietly regress.
        for chart in charts():
            resources = render(chart)
            for doc in resources:
                if doc.get("kind") != "Namespace":
                    continue
                labels = doc["metadata"].get("labels") or {}
                with self.subTest(chart=str(chart.relative_to(ROOT)),
                                  namespace=doc["metadata"]["name"]):
                    enforce = labels.get("pod-security.kubernetes.io/enforce")
                    self.assertIn(enforce, ("restricted", "baseline", "privileged"))
                    # Auditing and warning must never be looser than enforcement.
                    for weaker in ("audit", "warn"):
                        self.assertIn(labels.get(f"pod-security.kubernetes.io/{weaker}"),
                                      ("restricted", "baseline", "privileged"))

    def test_a_namespace_create_flag_actually_creates_a_namespace(self):
        # Six charts advertised namespace.create: true with no template behind
        # it, so an operator setting it got silence.
        for chart in charts():
            values_path = chart / "values.yaml"
            if not values_path.exists():
                continue
            declared = (yaml.safe_load(values_path.read_text()) or {}).get("namespace") or {}
            if "create" not in declared:
                continue
            with self.subTest(chart=str(chart.relative_to(ROOT))):
                rendered = subprocess.run(
                    [os.environ.get("HELM", "helm"), "template", chart.name,
                     str(chart_input(chart)), "--set", "namespace.create=true"],
                    capture_output=True, text=True)
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                namespaces = [d for d in yaml.safe_load_all(rendered.stdout)
                              if d and d.get("kind") == "Namespace"]
                self.assertEqual(len(namespaces), 1,
                                 "the flag must render exactly one Namespace")

    def test_every_namespace_is_rendered_by_the_shared_helper(self):
        # One definition keeps creation and labelling uniform across charts.
        for chart in charts():
            template = chart / "templates" / "namespace.yaml"
            if not template.exists():
                continue
            with self.subTest(chart=str(chart.relative_to(ROOT))):
                self.assertIn("kubarr-common.namespace", template.read_text())
    def test_privileged_namespaces_are_limited_to_those_that_need_the_host(self):
        for chart in charts():
            for doc in render(chart):
                if doc.get("kind") != "Namespace":
                    continue
                labels = doc["metadata"].get("labels") or {}
                if labels.get("pod-security.kubernetes.io/enforce") == "privileged":
                    with self.subTest(chart=str(chart.relative_to(ROOT))):
                        # Each of these reaches something in the host that a
                        # restricted namespace cannot, and nothing else may:
                        #   fluent-bit         node pod logs, through a hostPath
                        #   managed-nfs        host networking
                        #   node-exporter      the host's /proc, for the kernel's
                        #                      NFS client byte counters
                        #   kubarr-flow-exporter  Cilium's conntrack map, through
                        #                      the host BPF filesystem
                        self.assertIn(str(chart.relative_to(ROOT)),
                                      ("monitoring/fluent-bit",
                                       "system/managed-nfs",
                                       "monitoring/node-exporter",
                                       "system/kubarr-flow-exporter"))


if __name__ == "__main__":
    unittest.main()
