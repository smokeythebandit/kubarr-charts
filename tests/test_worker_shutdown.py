"""Verify the worker remains singleton and has time to drain during shutdown."""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "system/kubarr-worker"


def render(*values):
    command = [
        os.environ.get("HELM", "helm"),
        "template",
        "worker-shutdown-test",
        str(chart_input(CHART)),
    ]
    for value in values:
        command.extend(["--set", value])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    resources = [resource for resource in yaml.safe_load_all(result.stdout) if resource]
    return next(resource for resource in resources if resource["kind"] == "Deployment")


class WorkerShutdownTests(unittest.TestCase):
    def test_worker_is_singleton_with_recreate_strategy(self):
        deployment = render()

        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})

    def test_default_termination_grace_period_allows_drain(self):
        deployment = render()

        self.assertEqual(
            deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"],
            660,
        )

    def test_termination_grace_period_is_configurable(self):
        deployment = render("terminationGracePeriodSeconds=720")

        self.assertEqual(
            deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"],
            720,
        )


if __name__ == "__main__":
    unittest.main()
