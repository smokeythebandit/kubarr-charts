"""shepherd.decide picks one step per Dependabot PR; chart_facts reads the trees."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_shepherd():
    spec = importlib.util.spec_from_file_location("shepherd", HERE / "ci" / "shepherd.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("shepherd", module)
    spec.loader.exec_module(module)
    return module


S = load_shepherd()
GREEN = {"helm": "success", "install": "success"}


def pr(**kw):
    base = dict(
        number=1, head_ref="dependabot/helm/media-server/radarr/x/y-1.0", head_sha="h",
        base_sha="b", author="dependabot[bot]", labels=set(),
        files=["media-server/radarr/values.yaml"], checks=dict(GREEN),
    )
    base.update(kw)
    return S.PullRequest(**base)


def facts(**kw):
    base = dict(chart_dir="media-server/radarr", category="media-server", stale=False, major=False)
    base.update(kw)
    return S.ChartFacts(**base)


class DecideTests(unittest.TestCase):
    def test_decide_ignores_humans(self):
        self.assertEqual(S.decide(pr(author="someone"), facts()), "ignore")

    def test_decide_ignores_actions_ecosystem(self):
        self.assertEqual(S.decide(pr(head_ref="dependabot/github_actions/actions/checkout-5"), None), "ignore")

    def test_decide_ignores_needs_human(self):
        self.assertEqual(S.decide(pr(labels={"needs-human"}, checks={"helm": "failure"}), facts()), "ignore")

    def test_decide_multi_chart_needs_human(self):
        self.assertEqual(S.decide(pr(), None), "needs-human")

    def test_decide_bump_only_when_stale(self):
        self.assertEqual(S.decide(pr(checks={}), facts(stale=True)), "bump")
        self.assertEqual(S.decide(pr(), facts(stale=False)), "merge")

    def test_decide_fix_on_failed_install(self):
        self.assertEqual(S.decide(pr(checks={"helm": "success", "install": "failure"}), facts()), "fix")

    def test_decide_fix_on_failed_helm(self):
        self.assertEqual(S.decide(pr(checks={"helm": "timed_out", "install": "in_progress"}), facts()), "fix")

    def test_decide_gives_up_after_three(self):
        labels = {f"ai-fix-attempt-{i}" for i in (1, 2, 3)}
        self.assertEqual(S.decide(pr(labels=labels, checks={"helm": "failure", "install": "success"}), facts()), "needs-human")

    def test_decide_waits_while_running(self):
        self.assertEqual(S.decide(pr(checks={"helm": "success", "install": "in_progress"}), facts()), "wait")
        self.assertEqual(S.decide(pr(checks={"helm": "success"}), facts()), "wait")
        self.assertEqual(S.decide(pr(checks={}), facts()), "wait")

    def test_decide_merges_green_minor(self):
        self.assertEqual(S.decide(pr(), facts()), "merge")

    def test_decide_holds_green_major(self):
        self.assertEqual(S.decide(pr(), facts(major=True)), "hold-major")

    def test_decide_holds_green_platform(self):
        f = facts(chart_dir="system/foo", category="system", major=True)
        self.assertEqual(S.decide(pr(), f), "hold-platform")
        self.assertEqual(S.decide(pr(), facts(category="monitoring")), "hold-platform")

    def test_decide_ignores_already_held(self):
        self.assertEqual(S.decide(pr(labels={"major-update"}), facts(major=True)), "ignore")
        self.assertEqual(S.decide(pr(labels={"platform-update"}), facts(category="system")), "ignore")

    def test_fix_step_failure_does_not_count_attempt(self):
        self.assertEqual(S.decide(pr(labels={"ai-fix-attempt-1"}, checks={"helm": "success", "install": "failure"}), facts()), "fix")


def write_chart(root, tag, app_version, calver=False):
    d = Path(root) / "media-server" / "radarr"
    d.mkdir(parents=True)
    ann = "annotations:\n  kubarr.io/versioning: calver\n" if calver else ""
    (d / "Chart.yaml").write_text(
        f'apiVersion: v2\nname: radarr\ntype: application\nversion: {app_version}+3\nappVersion: "{app_version}"\n{ann}'
    )
    (d / "values.yaml").write_text(f"radarr:\n  image:\n    repository: x/radarr\n    tag: {tag}\n")


class ChartFactsTests(unittest.TestCase):
    def test_chart_facts_major_and_stale(self):
        with tempfile.TemporaryDirectory() as head, tempfile.TemporaryDirectory() as base:
            write_chart(base, "6.4.4", "6.4.4")
            write_chart(head, "7.0.3", "6.4.4")
            got = S.chart_facts(Path(head), Path(base), ["media-server/radarr/values.yaml"])
            self.assertEqual(got, S.ChartFacts("media-server/radarr", "media-server", True, True))

    def test_chart_facts_calver_never_major(self):
        with tempfile.TemporaryDirectory() as head, tempfile.TemporaryDirectory() as base:
            write_chart(base, "6.4.4", "6.4.4", calver=True)
            write_chart(head, "7.0.3", "7.0.3", calver=True)
            got = S.chart_facts(Path(head), Path(base), ["media-server/radarr/values.yaml"])
            self.assertFalse(got.major)
            self.assertFalse(got.stale)

    def test_chart_facts_missing_base_is_none(self):
        with tempfile.TemporaryDirectory() as head, tempfile.TemporaryDirectory() as base:
            write_chart(head, "7.0.3", "7.0.3")
            got = S.chart_facts(Path(head), Path(base), ["media-server/radarr/values.yaml"])
            self.assertIsNone(got)
            self.assertEqual(S.decide(pr(), got), "needs-human")

    def test_chart_facts_unreadable_base_tag_is_none(self):
        with tempfile.TemporaryDirectory() as head, tempfile.TemporaryDirectory() as base:
            write_chart(head, "7.0.3", "7.0.3")
            write_chart(base, "6.4.4", "6.4.4")
            (Path(base) / "media-server/radarr/values.yaml").write_text("radarr: {}\n")
            self.assertIsNone(S.chart_facts(Path(head), Path(base), ["media-server/radarr/values.yaml"]))

    def test_chart_facts_none_for_multi_or_outside(self):
        with tempfile.TemporaryDirectory() as head, tempfile.TemporaryDirectory() as base:
            write_chart(head, "1.0", "1.0")
            for files in (["a/b/x", "c/d/y"], ["media-server/radarr/values.yaml", ".github/workflows/ci.yml"], ["README.md"], []):
                self.assertIsNone(S.chart_facts(Path(head), Path(base), files))


if __name__ == "__main__":
    unittest.main()
