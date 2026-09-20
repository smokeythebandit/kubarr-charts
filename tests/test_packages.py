"""Verify every release archive is self-contained, identifiable, and renderable."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


class PackageTests(unittest.TestCase):
    def test_all_packages_have_expected_metadata_and_render(self):
        helm = os.environ.get("HELM", "helm")
        charts = sorted(ROOT.glob("*/*/Chart.yaml"))
        self.assertTrue(charts)
        expected = set()
        with tempfile.TemporaryDirectory(prefix="kubarr-packages-") as directory:
            for chart in charts:
                metadata = yaml.safe_load(chart.read_text())
                filename = f"{metadata['name']}-{metadata['version']}.tgz"
                self.assertNotIn(filename, expected, "Duplicate chart name/version")
                expected.add(filename)
                with self.subTest(chart=metadata["name"]):
                    if "CHART_PACKAGES_DIR" in os.environ:
                        package = chart_input(chart.parent)
                    else:
                        subprocess.run(
                            [helm, "package", str(chart.parent), "--destination", directory],
                            check=True, capture_output=True, text=True,
                        )
                        package = Path(directory) / filename
                    actual = yaml.safe_load(subprocess.check_output(
                        [helm, "show", "chart", str(package)], text=True,
                    ))
                    for key, value in metadata.items():
                        self.assertEqual(actual.get(key), value, key)
                    if metadata.get("type") == "library":
                        continue
                    rendered = subprocess.check_output(
                        [helm, "template", "package-test", str(package),
                         "--kube-version", "1.35.8"], text=True,
                    )
                    resources = [r for r in yaml.safe_load_all(rendered) if r]
                    self.assertTrue(resources, "Application package rendered no resources")
                    for resource in resources:
                        self.assertIn("apiVersion", resource)
                        self.assertIn("kind", resource)
                        self.assertIn("name", resource["metadata"])
            if "CHART_PACKAGES_DIR" in os.environ:
                actual = {p.name for p in Path(os.environ["CHART_PACKAGES_DIR"]).glob("*.tgz")}
                self.assertEqual(actual, expected, "Release package inventory mismatch")


if __name__ == "__main__":
    unittest.main()
