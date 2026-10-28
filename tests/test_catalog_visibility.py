"""System components must not advertise a web UI they do not serve."""

from pathlib import Path
import unittest

import yaml

from chart_artifacts import ROOT


def annotations(chart):
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text()) or {}
    return metadata.get("annotations") or {}


def charts():
    return sorted(p.parent for p in ROOT.glob("*/*/Chart.yaml"))


class CatalogVisibilityTests(unittest.TestCase):
    def test_no_system_component_can_be_opened_from_the_dashboard(self):
        # The dashboard opens any listed app whose chart does not refuse it.
        # Fluent Bit and PostgreSQL once inherited an "open" tile this way. A
        # component is safe if it is hidden outright or refuses browsing.
        for chart in charts():
            data = annotations(chart)
            if data.get("kubarr.io/system", "").lower() != "true":
                continue
            with self.subTest(chart=chart.relative_to(ROOT)):
                hidden = data.get("kubarr.io/hidden", "").lower() == "true"
                refused = data.get("kubarr.io/browseable", "").lower() == "false"
                self.assertTrue(
                    hidden or refused,
                    'a system component must set kubarr.io/browseable: "false"'
                    ' or kubarr.io/hidden: "true"')

    def test_application_charts_do_not_claim_to_be_system(self):
        for chart in charts():
            data = annotations(chart)
            if not data:
                continue
            with self.subTest(chart=chart.relative_to(ROOT)):
                self.assertIn(data.get("kubarr.io/system", "false").lower(),
                              ("true", "false"))


if __name__ == "__main__":
    unittest.main()
