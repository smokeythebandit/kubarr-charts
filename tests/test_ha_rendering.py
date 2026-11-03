"""System components scale across nodes only when kubarr.ha.nodeCount says so."""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input, credential_args


# chart -> (deployment name, replica cap at high node counts)
CHARTS = {
    "openresty": ("openresty", 3),
    "kubarr-backend": ("kubarr-backend", 3),
    "kubarr-frontend": ("kubarr-frontend", 2),
    "kubarr-worker": ("kubarr-worker", 2),
    "kubarr-dns-webhook": ("kubarr-dns-webhook", 2),
}


def render(name, *values):
    chart = ROOT / "system" / name
    command = ["helm", "template", "ha-test", str(chart_input(chart)), *credential_args(chart)]
    for value in values:
        command.extend(["--set", value])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [r for r in yaml.safe_load_all(result.stdout) if r]


def of_kind(resources, kind):
    return [r for r in resources if r["kind"] == kind]


def deployment(resources):
    return of_kind(resources, "Deployment")[0]


def assert_spread(case, dep):
    case.assertEqual(
        dep["spec"]["template"]["spec"]["topologySpreadConstraints"],
        [{
            "maxSkew": 1,
            "topologyKey": "kubernetes.io/hostname",
            "whenUnsatisfiable": "DoNotSchedule",
            # Tainted nodes (control planes, unreachable) are not spread
            # domains, so a rollout's surge pod is never held at skew 2 - 0.
            "nodeTaintsPolicy": "Honor",
            # Count only pods of the same ReplicaSet, not the old ones a
            # rolling update is about to delete.
            "matchLabelKeys": ["pod-template-hash"],
            "labelSelector": {"matchLabels": dep["spec"]["selector"]["matchLabels"]},
        }],
    )


class HaRenderingTests(unittest.TestCase):
    def test_single_node_keeps_single_replica_shape(self):
        for name in CHARTS:
            for values in ((), ("kubarr.ha.nodeCount=1",), ("kubarr.ha.nodeCount=0",)):
                with self.subTest(chart=name, values=values):
                    resources = render(name, *values)
                    dep = deployment(resources)
                    self.assertEqual(dep["spec"]["replicas"], 1)
                    self.assertNotIn("topologySpreadConstraints", dep["spec"]["template"]["spec"])
                    self.assertEqual(of_kind(resources, "PodDisruptionBudget"), [])

    def test_two_nodes_spread_and_protect(self):
        for name in CHARTS:
            with self.subTest(chart=name):
                resources = render(name, "kubarr.ha.nodeCount=2")
                dep = deployment(resources)
                self.assertEqual(dep["spec"]["replicas"], 2)
                assert_spread(self, dep)
                (pdb,) = of_kind(resources, "PodDisruptionBudget")
                self.assertEqual(pdb["spec"]["maxUnavailable"], 1)
                self.assertEqual(
                    pdb["spec"]["selector"]["matchLabels"],
                    dep["spec"]["selector"]["matchLabels"],
                )
                self.assertEqual(pdb["metadata"]["namespace"], dep["metadata"]["namespace"])

    def test_replicas_are_capped_per_chart(self):
        for name, (_, cap) in CHARTS.items():
            with self.subTest(chart=name):
                dep = deployment(render(name, "kubarr.ha.nodeCount=5"))
                self.assertEqual(dep["spec"]["replicas"], cap)

    def test_three_nodes_reach_the_three_replica_tier(self):
        for name in ("openresty", "kubarr-backend"):
            with self.subTest(chart=name):
                self.assertEqual(deployment(render(name, "kubarr.ha.nodeCount=3"))["spec"]["replicas"], 3)

    def test_cloudflared_is_always_two_and_spread(self):
        for values in ((), ("kubarr.ha.nodeCount=1",), ("kubarr.ha.nodeCount=5",)):
            with self.subTest(values=values):
                resources = render("cloudflared", *values)
                dep = deployment(resources)
                self.assertEqual(dep["spec"]["replicas"], 2)
                assert_spread(self, dep)
                (pdb,) = of_kind(resources, "PodDisruptionBudget")
                self.assertEqual(pdb["spec"]["maxUnavailable"], 1)


class WorkerLeaderTests(unittest.TestCase):
    def test_rolling_update_without_overlap_loss(self):
        dep = deployment(render("kubarr-worker"))
        self.assertEqual(
            dep["spec"]["strategy"],
            {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
        )

    def test_pod_identity_from_downward_api(self):
        env = {e["name"]: e for e in deployment(render("kubarr-worker"))["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["POD_NAME"]["valueFrom"]["fieldRef"]["fieldPath"], "metadata.name")
        self.assertEqual(env["POD_NAMESPACE"]["valueFrom"]["fieldRef"]["fieldPath"], "metadata.namespace")

    def test_lease_role_in_worker_namespace(self):
        resources = render("kubarr-worker")
        dep = deployment(resources)
        namespace = dep["metadata"]["namespace"]
        (role,) = of_kind(resources, "Role")
        self.assertEqual(role["metadata"]["namespace"], namespace)
        (rule,) = role["rules"]
        self.assertEqual(rule["apiGroups"], ["coordination.k8s.io"])
        self.assertEqual(rule["resources"], ["leases"])
        self.assertEqual(sorted(rule["verbs"]), ["create", "get", "update"])
        (binding,) = of_kind(resources, "RoleBinding")
        self.assertEqual(binding["roleRef"]["name"], role["metadata"]["name"])
        self.assertEqual(binding["subjects"][0]["namespace"], namespace)

    def test_cluster_role_reads_nodes(self):
        (cluster_role,) = of_kind(render("kubarr-worker"), "ClusterRole")
        verbs = set()
        for rule in cluster_role["rules"]:
            if rule.get("apiGroups") == [""] and "nodes" in rule["resources"]:
                verbs |= set(rule["verbs"])
        self.assertTrue({"get", "list", "watch"} <= verbs)


if __name__ == "__main__":
    unittest.main()
