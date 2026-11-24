"""Bundled Postgres must not report ready while its entrypoint is initializing."""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input, credential_args


# The postgres image's entrypoint runs initdb and the role/database setup on a
# temporary server that listens on the Unix socket only. A socket pg_isready
# therefore passes mid-initialization, and a restart at that moment leaves a
# data directory the entrypoint never initializes again (no application role).
# Probing over TCP only passes once the real server is up.
CHARTS = ["development/redmine", "media-server/immich"]


def postgres_containers(chart):
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", "probe-test", str(chart_input(ROOT / chart)),
         *credential_args(ROOT / chart)],
        capture_output=True, text=True, check=True,
    )
    for doc in yaml.safe_load_all(result.stdout):
        if not doc or doc.get("kind") != "StatefulSet":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            if "postgres" in container["image"]:
                yield doc["metadata"]["name"], container


class BundledPostgresProbeTests(unittest.TestCase):
    def test_probes_use_tcp_not_the_init_socket(self):
        for chart in CHARTS:
            found = list(postgres_containers(chart))
            self.assertTrue(found, chart)
            for name, container in found:
                for probe in ("startupProbe", "readinessProbe"):
                    command = " ".join(container[probe]["exec"]["command"])
                    with self.subTest(chart=chart, workload=name, probe=probe):
                        self.assertIn("pg_isready -h 127.0.0.1", command)


if __name__ == "__main__":
    unittest.main()
