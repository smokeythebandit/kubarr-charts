"""Verify NFS-backed monitoring stores get enough time to initialize."""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHARTS = (
    (ROOT / "monitoring/victorialogs", "victorialogs"),
    (ROOT / "monitoring/victoriametrics", "victoriametrics"),
)


def render(chart, release, *values):
    command = [
        os.environ.get("HELM", "helm"),
        "template",
        release,
        str(chart_input(chart)),
    ]
    for value in values:
        command.extend(["--set", value])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def deployment(resources):
    return next(resource for resource in resources if resource["kind"] == "Deployment")


class MonitoringStartupTests(unittest.TestCase):
    def test_startup_probe_guards_existing_health_probes(self):
        for chart, application in CHARTS:
            with self.subTest(chart=chart.name):
                resources = render(chart, chart.name)
                workload = deployment(resources)
                container, = workload["spec"]["template"]["spec"]["containers"]
                volume = next(
                    volume
                    for volume in workload["spec"]["template"]["spec"]["volumes"]
                    if volume["name"] == "data"
                )

                self.assertIn("persistentVolumeClaim", volume)
                for probe_name in ("startupProbe", "readinessProbe", "livenessProbe"):
                    self.assertEqual(
                        container[probe_name]["httpGet"],
                        {"path": "/health", "port": "http"},
                    )
                self.assertEqual(container["startupProbe"]["periodSeconds"], 2)
                self.assertEqual(container["startupProbe"]["timeoutSeconds"], 2)
                self.assertEqual(container["startupProbe"]["failureThreshold"], 90)
                self.assertEqual(container["name"], application)

    def test_startup_probe_is_configurable(self):
        for chart, application in CHARTS:
            with self.subTest(chart=chart.name):
                values = (
                    f"{application}.startupProbe.periodSeconds=3",
                    f"{application}.startupProbe.timeoutSeconds=4",
                    f"{application}.startupProbe.failureThreshold=50",
                )
                workload = deployment(render(chart, chart.name, *values))
                container, = workload["spec"]["template"]["spec"]["containers"]

                self.assertEqual(container["startupProbe"]["periodSeconds"], 3)
                self.assertEqual(container["startupProbe"]["timeoutSeconds"], 4)
                self.assertEqual(container["startupProbe"]["failureThreshold"], 50)

    def test_startup_probe_can_be_disabled(self):
        for chart, application in CHARTS:
            with self.subTest(chart=chart.name):
                workload = deployment(
                    render(chart, chart.name, f"{application}.startupProbe.enabled=false")
                )
                container, = workload["spec"]["template"]["spec"]["containers"]

                self.assertNotIn("startupProbe", container)
                self.assertIn("readinessProbe", container)
                self.assertIn("livenessProbe", container)


if __name__ == "__main__":
    unittest.main()
