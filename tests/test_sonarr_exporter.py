"""Verify Exportarr waits for Sonarr's generated API key before startup."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "media-manager/sonarr"


def render():
    result = subprocess.run(
        [
            os.environ.get("HELM", "helm"),
            "template",
            "sonarr-exporter-test",
            str(chart_input(CHART)),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    resources = [resource for resource in yaml.safe_load_all(result.stdout) if resource]
    return next(
        resource
        for resource in resources
        if resource["kind"] == "Deployment"
        and resource["metadata"]["name"].endswith("-exporter")
    )


def startup_script():
    deployment = render()
    init_container, = deployment["spec"]["template"]["spec"]["initContainers"]
    return deployment, init_container, init_container["command"][2]


def write_executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


class SonarrExporterTests(unittest.TestCase):
    def test_defaults_preserve_exporter_and_add_config_wait(self):
        deployment, init_container, _ = startup_script()
        exporter, = deployment["spec"]["template"]["spec"]["containers"]

        self.assertEqual(init_container["image"], "linuxserver/sonarr:4.0.19")
        self.assertEqual(init_container["command"][:2], ["/bin/sh", "-c"])
        self.assertEqual(exporter["image"], "ghcr.io/onedr0p/exportarr:v2.3.0")
        self.assertEqual(exporter["args"], ["sonarr", "--config=/config/config.xml"])
        self.assertEqual(
            deployment["spec"]["template"]["metadata"]["labels"],
            {
                "app.kubernetes.io/name": "sonarr-exporter",
                "app.kubernetes.io/instance": "sonarr-exporter-test",
            },
        )
        self.assertIn("livenessProbe", exporter)
        self.assertIn("readinessProbe", exporter)

    def test_waits_for_generated_key_without_logging_it(self):
        _, _, script = startup_script()
        api_key = "GeneratedApiKey123"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.xml"
            config.write_text("<Config></Config>\n")
            calls = root / "sed-calls"
            sleeps = root / "sleep-calls"
            write_executable(
                root / "sed",
                "#!/bin/sh\n"
                'count=$(($(cat "$MOCK_SED_CALLS" 2>/dev/null || printf 0) + 1))\n'
                'printf "%s" "$count" >"$MOCK_SED_CALLS"\n'
                '[ "$count" -lt 3 ] || printf "%s\\n" "$MOCK_API_KEY"\n',
            )
            write_executable(
                root / "sleep",
                "#!/bin/sh\n"
                'count=$(($(cat "$MOCK_SLEEP_CALLS" 2>/dev/null || printf 0) + 1))\n'
                'printf "%s" "$count" >"$MOCK_SLEEP_CALLS"\n',
            )
            environment = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "MOCK_API_KEY": api_key,
                "MOCK_SED_CALLS": str(calls),
                "MOCK_SLEEP_CALLS": str(sleeps),
                "SONARR_CONFIG_FILE": str(config),
                "SONARR_CONFIG_WAIT_ATTEMPTS": "4",
                "SONARR_CONFIG_WAIT_INTERVAL": "0",
            }

            result = subprocess.run(
                ["/bin/sh", "-c", script],
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text(), "3")
            self.assertEqual(sleeps.read_text(), "2")
            self.assertNotIn(api_key, result.stdout + result.stderr)

    def test_config_read_error_status_is_preserved(self):
        _, _, script = startup_script()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.xml"
            config.write_text("<Config></Config>\n")
            write_executable(root / "sed", "#!/bin/sh\nexit 23\n")
            environment = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "SONARR_CONFIG_FILE": str(config),
                "SONARR_CONFIG_WAIT_ATTEMPTS": "1",
            }

            result = subprocess.run(
                ["/bin/sh", "-c", script],
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 23)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "Failed to read Sonarr configuration\n")

    def test_wait_is_bounded_and_sigterm_stops_sleep(self):
        _, _, script = startup_script()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.xml"
            config.write_text("<Config><ApiKey></ApiKey></Config>\n")
            marker = root / "sleeping"
            write_executable(
                root / "sleep",
                "#!/bin/sh\n"
                'touch "$MOCK_SLEEP_MARKER"\n'
                "exec /bin/sleep 30\n",
            )
            environment = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "MOCK_SLEEP_MARKER": str(marker),
                "SONARR_CONFIG_FILE": str(config),
                "SONARR_CONFIG_WAIT_ATTEMPTS": "2",
                "SONARR_CONFIG_WAIT_INTERVAL": "30",
            }
            process = subprocess.Popen(
                ["/bin/sh", "-c", script],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "wait script never entered its bounded sleep")

            process.terminate()
            stdout, stderr = process.communicate(timeout=2)

            self.assertEqual(process.returncode, 143)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
