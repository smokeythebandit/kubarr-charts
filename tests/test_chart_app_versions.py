"""Chart versions must carry the application version as their core.

The publish workflow refuses a chart whose version core (before `+`) differs
from its normalized appVersion (`v` stripped, missing minor/patch as 0), and a
refused chart blocks every chart from being published. Build bumps belong in
the `+N` suffix, never in the core.
"""

import unittest

import yaml

from chart_artifacts import ROOT


def normalized(app_version):
    core = str(app_version).lstrip("v").split("+", 1)[0]
    parts = core.split(".")
    major = parts[0]
    minor = parts[1] if len(parts) > 1 and parts[1] else "0"
    patch = parts[2] if len(parts) > 2 and parts[2] else "0"
    return f"{major}.{minor}.{patch}"


class ChartAppVersionTests(unittest.TestCase):
    def test_version_core_matches_app_version(self):
        for chart_yaml in sorted(ROOT.glob("*/*/Chart.yaml")):
            meta = yaml.safe_load(chart_yaml.read_text())
            if meta.get("type") == "library" or "appVersion" not in meta:
                continue
            with self.subTest(chart=str(chart_yaml.parent.relative_to(ROOT))):
                core = str(meta["version"]).split("+", 1)[0]
                self.assertEqual(core, normalized(meta["appVersion"]),
                                 "bump the +N build suffix, not the version core")


if __name__ == "__main__":
    unittest.main()
