"""bump_chart derives chart version/appVersion from the primary image tag."""

import importlib.util
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_bump():
    spec = importlib.util.spec_from_file_location("bump_chart", HERE / "ci" / "bump_chart.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("bump_chart", module)
    spec.loader.exec_module(module)
    return module


CHART = """\
apiVersion: v2
name: radarr
# keep me
type: application
version: 6.4.4+25
appVersion: "6.4.4"
"""

VALUES = """\
radarr:
  image:
    repository: lscr.io/linuxserver/radarr
    tag: "{tag}"
vpn:
  image:
    repository: qmcgaw/gluetun
    tag: "v3.40.0"
exporter:
  image:
    repository: example/exporter
    tag: "{exporter}"
"""


@unittest.skipIf(yaml is None, "PyYAML is required")
class BumpChart(unittest.TestCase):
    def setUp(self):
        self.mod = load_bump()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_chart(self, chart=CHART, tag="7.0.3", exporter="1.0.0"):
        d = self.tmp / "radarr"
        d.mkdir(exist_ok=True)
        (d / "Chart.yaml").write_text(chart)
        (d / "values.yaml").write_text(VALUES.format(tag=tag, exporter=exporter))
        return d

    def test_normalize_tag(self):
        cases = {"3.09.00": "3.9.0", "2.591-jdk21": "2.591", "12.3ubu2604-ls56": "12.3",
                 "v3.10-ls122": "3.10", "1.31.8.1-alpine": "1.31.8.1", "v0.35.0": "0.35.0",
                 "7.0.2-bookworm": "7.0.2", "2026.11.1": "2026.11.1"}
        for tag, expected in cases.items():
            self.assertEqual(self.mod.normalize_tag(tag), expected, tag)
        with self.assertRaises(ValueError):
            self.mod.normalize_tag("latest")

    def test_version_core(self):
        self.assertEqual(self.mod.version_core("2.591"), "2.591.0")
        self.assertEqual(self.mod.version_core("1.31.8.1"), "1.31.8")

    def test_bump_rewrites_only_version_lines(self):
        chart = self.make_chart()
        self.assertTrue(self.mod.bump(chart))
        text = (chart / "Chart.yaml").read_text()
        self.assertIn("version: 7.0.3+26\n", text)
        self.assertIn('appVersion: "7.0.3"\n', text)
        self.assertIn("# keep me", text)
        self.assertEqual(text.splitlines()[:4], CHART.splitlines()[:4])

    def test_bump_noop_when_already_current(self):
        chart = self.make_chart()
        self.mod.bump(chart)
        before = (chart / "Chart.yaml").read_text()
        self.assertFalse(self.mod.bump(chart))
        self.assertEqual((chart / "Chart.yaml").read_text(), before)

    def test_v_prefixed_app_version_is_current(self):
        chart = self.make_chart(chart=CHART.replace('appVersion: "6.4.4"', 'appVersion: "v7.0.3"'))
        before = (chart / "Chart.yaml").read_text()
        self.assertFalse(self.mod.bump(chart))
        self.assertEqual((chart / "Chart.yaml").read_text(), before)

    def test_sidecar_tag_ignored(self):
        chart = self.make_chart(tag="6.4.4", exporter="9.9.9")
        self.assertFalse(self.mod.bump(chart))

    def test_annotation_overrides_primary(self):
        annotated = CHART + "annotations:\n  kubarr.io/primary-image: vpn.image\n"
        chart = self.make_chart(chart=annotated)
        self.assertEqual(self.mod.primary_image_path(chart), ["vpn", "image"])
        self.assertEqual(self.mod.primary_tag(chart), "v3.40.0")

    def test_default_primary_is_chart_name(self):
        self.assertEqual(self.mod.primary_image_path(self.make_chart()), ["radarr", "image"])

    def test_no_primary_image_raises(self):
        chart = self.make_chart(chart=CHART.replace("name: radarr", "name: other"))
        with self.assertRaises(ValueError):
            self.mod.primary_image_path(chart)

    def test_is_major(self):
        self.assertTrue(self.mod.is_major("6.4.4", "7.0.3", calver=False))
        self.assertFalse(self.mod.is_major("1.29.2", "1.30.1", calver=False))
        self.assertFalse(self.mod.is_major("2026.10.3", "2027.1.0", calver=True))

    def test_every_application_chart_has_a_primary_image(self):
        failures = []
        checked = 0
        for chart_file in sorted(ROOT.glob("*/*/Chart.yaml")):
            d = chart_file.parent
            if (yaml.safe_load(chart_file.read_text()) or {}).get("type") != "application":
                continue
            values = d / "values.yaml"
            if not values.exists() or not self._has_image_block(yaml.safe_load(values.read_text())):
                continue
            checked += 1
            try:
                self.mod.normalize_tag(self.mod.primary_tag(d))
            except (ValueError, KeyError) as err:
                failures.append(f"{d.relative_to(ROOT)}: {err}")
        self.assertGreater(checked, 20)
        self.assertEqual(failures, [])

    def _has_image_block(self, node):
        if isinstance(node, dict):
            if "repository" in node and "tag" in node:
                return True
            return any(self._has_image_block(v) for v in node.values())
        if isinstance(node, list):
            return any(self._has_image_block(v) for v in node)
        return False


if __name__ == "__main__":
    unittest.main()
