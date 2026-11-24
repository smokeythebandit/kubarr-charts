"""The install gate's summary is rendered from results, so shards can be merged."""

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "e2e" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


gate = load("install_gate")
merge = load("merge_gate_results")
SKIPPED_CHART = "kubarr-flow-exporter"  # a name in install_gate.SKIPS


def rec(name):
    return {"chart": name, "install": "pass", "from": "1.0.0", "upgrade": "pass",
            "seconds": {"install": 1.0, "upgrade": 1.0}}


class RenderSummary(unittest.TestCase):
    def test_render_summary_all_pass(self):
        text, ok = gate.render_summary(
            [{"chart": "komga", "install": "pass", "from": "1.29.2+5", "upgrade": "pass",
              "seconds": {"install": 30.0, "upgrade": 20.0}}],
            ["komga"], {}, [], 50.0, "Sharded run")
        self.assertTrue(ok)
        self.assertIn("| `komga` | pass | `1.29.2+5` | pass | 30.0+20.0 |", text)
        self.assertIn("Selected 1 chart(s); 1 passed, 0 failed; total 50.0s.", text)

    def test_missing_shard_results_fail(self):
        text, ok = gate.render_summary([], ["komga", "frigate"], {}, [], 0.0, "x")
        self.assertFalse(ok)
        self.assertIn("**Never reached (2):** `komga`, `frigate`", text)

    def test_record_ok_rules(self):
        self.assertTrue(gate.record_ok(dict(rec("a"), upgrade="skipped: new")))
        self.assertTrue(gate.record_ok(dict(rec("a"), upgrade="baseline-broken: x")))
        self.assertFalse(gate.record_ok(dict(rec("a"), install="boom")))
        self.assertFalse(gate.record_ok(dict(rec("a"), upgrade="boom")))


class Merge(unittest.TestCase):
    def test_skips_listed_once(self):
        skipped = {SKIPPED_CHART: "reason"}
        merged = merge.merge(
            planned={"targets": ["a", "b"], "skipped": skipped, "shards": [["a"], ["b"]]},
            shard_results=[{"results": [rec("a")], "timings": [{"seconds": 1.0}], "skipped": skipped},
                           {"results": [rec("b")], "timings": [{"seconds": 2.0}], "skipped": skipped}])
        self.assertEqual(merged["text"].count(f"`{SKIPPED_CHART}` (selected)"), 1)
        self.assertTrue(merged["ok"])
        self.assertIn("total 3.0s", merged["text"])
        self.assertIn("Sharded run: 2 shard(s)", merged["text"])

    def test_failed_upgrade_fails_merge(self):
        bad = dict(rec("a"), upgrade="redmine upgrade to local failed: helm upgrade exited 1")
        merged = merge.merge(
            planned={"targets": ["a"], "skipped": {}, "shards": [["a"]]},
            shard_results=[{"results": [bad], "timings": [], "skipped": {}}])
        self.assertFalse(merged["ok"])
        self.assertIn("**FAIL**", merged["text"])

    def test_empty_selection_passes(self):
        merged = merge.merge(
            planned={"targets": [], "skipped": {SKIPPED_CHART: "why"}, "shards": []},
            shard_results=[])
        self.assertTrue(merged["ok"])
        self.assertIn("## Chart install gate\n\nNo chart selected; nothing installed.",
                      merged["text"])
        self.assertIn(f"- skipped by name: `{SKIPPED_CHART}` - why", merged["text"])


if __name__ == "__main__":
    unittest.main()
