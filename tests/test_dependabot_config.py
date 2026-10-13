"""Keep Dependabot chart coverage and local exclusions in sync with the repo."""

from fnmatch import fnmatchcase
import json
import unittest

import yaml

from chart_artifacts import ROOT


class DependabotConfigTests(unittest.TestCase):
    def test_chart_coverage_and_local_exclusions(self):
        config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
        self.assertEqual(config["version"], 2)
        helm, actions = config["updates"]
        self.assertEqual([helm["package-ecosystem"], actions["package-ecosystem"]],
                         ["helm", "github-actions"])

        charts = list(ROOT.glob("*/*/Chart.yaml"))
        self.assertTrue(charts)
        chart_dirs = {"/" + str(path.parent.relative_to(ROOT)) for path in charts}
        patterns = helm["directories"]
        self.assertEqual({path for pattern in patterns for path in chart_dirs
                          if fnmatchcase(path, pattern)}, chart_dirs)
        for chart_dir in chart_dirs:
            self.assertEqual(sum(fnmatchcase(chart_dir, pattern) for pattern in patterns), 1,
                             chart_dir)
        self.assertEqual({pattern.removesuffix("/*") for pattern in patterns},
                         {"/" + path.parent.parent.name for path in charts})

        ignored = {rule["dependency-name"] for rule in helm["ignore"]}
        local_dependencies = {
            dep["name"] for path in charts
            for dep in yaml.safe_load(path.read_text()).get("dependencies", [])
            if dep["repository"].startswith("file://")
        }
        local_images = {
            values["image"]["repository"]
            for path in (ROOT / "system").glob("*/values.yaml")
            if (values := yaml.safe_load(path.read_text())).get("image", {}).get("pullPolicy") == "Never"
        }
        self.assertEqual(ignored, local_dependencies | local_images)
        self.assertFalse(json.loads((ROOT / "renovate.json").read_text())["enabled"])

    def test_schedule_and_actions(self):
        helm, actions = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]
        for update in (helm, actions):
            self.assertEqual(update["schedule"], {
                "interval": "weekly", "day": "monday", "time": "09:00",
                "timezone": "Etc/UTC",
            })
            self.assertGreater(update["open-pull-requests-limit"], 0)
        self.assertEqual(helm["open-pull-requests-limit"], 5)
        self.assertEqual(actions["directory"], "/")
        self.assertEqual(actions["groups"]["actions"]["patterns"], ["*"])


if __name__ == "__main__":
    unittest.main()
