"""Build file dependencies without the ignored, locally cached archives."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT


class DependencyTests(unittest.TestCase):
    def test_common_consumers_build_from_clean_charts(self):
        common = ROOT / "system/kubarr-common"
        version = yaml.safe_load((common / "Chart.yaml").read_text())["version"]
        charts = sorted(ROOT.glob("*/*/Chart.yaml"))
        consumers = [p.parent for p in charts if any(
            dep["name"] == "kubarr-common"
            for dep in yaml.safe_load(p.read_text()).get("dependencies", [])
        )]
        self.assertTrue(consumers)

        with tempfile.TemporaryDirectory(prefix="kubarr-clean-deps-") as directory:
            target = Path(directory)
            shutil.copytree(common, target / "system/kubarr-common",
                            ignore=shutil.ignore_patterns("charts"))
            for chart in consumers:
                with self.subTest(chart=chart.relative_to(ROOT)):
                    metadata = yaml.safe_load((chart / "Chart.yaml").read_text())
                    dependency = next(dep for dep in metadata["dependencies"]
                                      if dep["name"] == "kubarr-common")
                    lock = yaml.safe_load((chart / "Chart.lock").read_text())
                    locked = next(dep for dep in lock["dependencies"]
                                  if dep["name"] == "kubarr-common")
                    self.assertEqual(dependency, locked)
                    self.assertEqual(dependency["version"], version)

                    fresh = target / chart.relative_to(ROOT)
                    shutil.copytree(chart, fresh, ignore=shutil.ignore_patterns("charts"))
                    result = subprocess.run(
                        [os.environ.get("HELM", "helm"), "dependency", "build",
                         "--skip-refresh", str(fresh)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((fresh / "Chart.lock").read_bytes(),
                                     (chart / "Chart.lock").read_bytes())
                    self.assertTrue((fresh / "charts" / f"kubarr-common-{version}.tgz").is_file())


if __name__ == "__main__":
    unittest.main()
