"""A declared call must be allowed at both ends.

The shared policy denies in-cluster traffic unless the caller names the
callee's namespace in `networkPolicy.egressTo` and the callee names the
caller in `networkPolicy.ingressFrom`. Only the internet (non-RFC1918) and
DNS are open by default, so a pod-to-pod call with one half missing fails at
connect time, long after the charts rendered happily.

That is how Seerr's connection to Radarr and Sonarr was dead on arrival: it
declared the egress, and neither manager admitted it.
"""

import os
from pathlib import Path
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


def charts():
    return sorted(p.parent for p in ROOT.glob("*/*/Chart.yaml"))


def policy_of(chart):
    path = chart / "values.yaml"
    if not path.is_file():
        return None, None
    values = yaml.safe_load(path.read_text()) or {}
    namespace = (values.get("namespace") or {}).get("name")
    return namespace, (values.get("networkPolicy") or {})


def topology():
    """Namespace -> chart, and each chart's declared peers."""
    owners, declared = {}, {}
    for chart in charts():
        namespace, policy = policy_of(chart)
        if policy is None:
            continue
        name = chart.name
        if namespace:
            owners[namespace] = name
        declared[name] = {
            "chart": chart,
            "namespace": namespace,
            "enabled": policy.get("enabled", True),
            # ingressFrom, or per-port lists such as Dispatcharr's uiFrom and
            # tunerFrom: any *From list admits those callers.
            "ingress": [peer for key, peers in policy.items()
                        if key.endswith("From") and isinstance(peers, list)
                        for peer in peers],
            "egress": list(policy.get("egressTo") or []),
        }
    return owners, declared


class ReachabilityTests(unittest.TestCase):
    def test_every_declared_egress_is_admitted_by_its_callee(self):
        owners, declared = topology()
        for name, peer in declared.items():
            for target_namespace in peer["egress"]:
                callee = owners.get(target_namespace)
                if callee is None:
                    continue  # not a chart in this repository
                with self.subTest(caller=name, callee=callee):
                    if not declared[callee]["enabled"]:
                        continue  # the callee ships no policy, so nothing denies
                    self.assertIn(
                        peer["namespace"] or name,
                        declared[callee]["ingress"],
                        f"{name} calls {callee}, which does not admit it")

    def test_the_rendered_policies_agree_with_the_declarations(self):
        # The values lists are the contract; this proves the template honours
        # them, so reading values.yaml elsewhere in this file is sound.
        owners, declared = topology()
        for name, peer in declared.items():
            if not peer["enabled"] or not peer["egress"]:
                continue
            with self.subTest(chart=name):
                rendered = subprocess.run(
                    [os.environ.get("HELM", "helm"), "template", name,
                     str(chart_input(peer["chart"]))],
                    check=True, capture_output=True, text=True,
                ).stdout
                allowed = set()
                for doc in yaml.safe_load_all(rendered):
                    if not doc or doc.get("kind") != "NetworkPolicy":
                        continue
                    for rule in doc["spec"].get("egress", []):
                        for entry in rule.get("to", []):
                            labels = entry.get("namespaceSelector", {}).get("matchLabels", {})
                            if "kubernetes.io/metadata.name" in labels:
                                allowed.add(labels["kubernetes.io/metadata.name"])
                self.assertEqual(set(peer["egress"]) - allowed, set())


if __name__ == "__main__":
    unittest.main()
