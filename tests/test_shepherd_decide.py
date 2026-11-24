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

    def test_chart_facts_v_prefixed_app_version_is_not_stale(self):
        with tempfile.TemporaryDirectory() as head, tempfile.TemporaryDirectory() as base:
            write_chart(base, "v0.0.36", "v0.0.36")
            write_chart(head, "v0.0.37", "v0.0.37")
            got = S.chart_facts(Path(head), Path(base), ["media-server/radarr/values.yaml"])
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


class RunnerTests(unittest.TestCase):
    def test_runner_dry_run_prints_actions(self):
        import io
        import json

        prs = [
            dict(number=34, headRefName="dependabot/helm/media-server/komga/x", headRefOid="h34", baseRefOid="b34",
                 labels=[], files=[{"path": "media-server/komga/values.yaml"}], author={"login": "dependabot"}),
            dict(number=32, headRefName="dependabot/helm/media-server/frigate/x", headRefOid="h32", baseRefOid="b32",
                 labels=[], files=[{"path": "media-server/frigate/values.yaml"}], author={"login": "dependabot"}),
        ]
        calls = []

        def fake_gh(*args):
            calls.append(args)
            if args[0] == "pr" and args[1] == "list":
                return json.dumps(prs)
            if args[0] == "api":
                return "".join(
                    json.dumps(dict(name=n, status="completed", conclusion="success", url=None)) + "\n"
                    for n in ("helm", "install"))
            raise AssertionError(f"unexpected gh call {args}")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def fake_checkout(sha, parent, name):
            root = Path(tmp.name) / sha
            chart = "komga" if sha.endswith("34") else "frigate"
            if not root.exists():
                tag = "2.0.0" if sha.startswith("b") else "2.1.0"
                d = root / "media-server" / chart
                d.mkdir(parents=True)
                stale_version = "2.0.0" if sha == "h34" else tag
                (d / "Chart.yaml").write_text(
                    f'apiVersion: v2\nname: {chart}\nversion: 1.0.0+1\nappVersion: "{stale_version}"\n')
                (d / "values.yaml").write_text(f"{chart}:\n  image:\n    repository: x/y\n    tag: {tag}\n")
            return root

        def no_commit(*a, **k):
            raise AssertionError("dry run must not commit")

        out = io.StringIO()
        S.run_shepherd("o/r", Path(tmp.name) / "fix.json", dry_run=True, run=fake_gh,
                       checkout=fake_checkout, commit=no_commit, out=out)
        self.assertIn("#34 bump", out.getvalue())
        self.assertIn("#32 merge", out.getvalue())
        self.assertFalse([c for c in calls if c[0] == "pr" and c[1] != "list"])
        self.assertFalse((Path(tmp.name) / "fix.json").exists())


def listed(number, chart, labels=()):
    return dict(number=number, headRefName=f"dependabot/helm/media-server/{chart}/x", headRefOid=f"h{number}",
                baseRefOid=f"b{number}", labels=[{"name": n} for n in labels],
                files=[{"path": f"media-server/{chart}/values.yaml"}], author={"login": "dependabot"})


class MutatingRunnerTests(unittest.TestCase):
    def setUp(self):
        import io
        import json
        self.json, self.out = json, io.StringIO()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls, self.prs, self.check_state = [], [], "success"

    def fake_gh(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return self.json.dumps(self.prs)
        if args[0] == "api":
            return "".join(
                self.json.dumps(dict(name=n, status="completed", conclusion=self.check_state,
                                     url="https://x/actions/runs/99/job/1")) + "\n"
                for n in ("helm", "install"))
        if args[:2] == ("run", "view"):
            return "LOGTEXT"
        return ""

    def checkout(self, sha, parent, name):
        root = Path(self.tmp.name) / sha
        if not root.exists():
            chart = {"34": "komga", "32": "frigate", "30": "sonarr"}[sha[1:]]
            d = root / "media-server" / chart
            d.mkdir(parents=True)
            base_tag, head_tag = ("1.0.0", "2.0.0") if sha[1:] == "30" else ("2.0.0", "2.1.0")
            tag = base_tag if sha.startswith("b") else head_tag
            app = base_tag if sha == "h34" else tag  # #34 is stale: the tag moved, appVersion did not
            (d / "Chart.yaml").write_text(f'apiVersion: v2\nname: {chart}\nversion: 1.0.0+1\nappVersion: "{app}"\n')
            (d / "values.yaml").write_text(f"{chart}:\n  image:\n    repository: x/y\n    tag: {tag}\n")
        return root

    def run_it(self, **kw):
        return S.run_shepherd("o/r", Path(self.tmp.name) / "fix.json", run=self.fake_gh,
                              checkout=self.checkout, out=self.out, **kw)

    def test_merge_invokes_gh_merge_with_head_guard(self):
        self.prs = [listed(32, "frigate")]
        self.run_it()
        self.assertIn(("pr", "merge", "32", "-R", "o/r", "--merge", "--match-head-commit", "h32"), self.calls)

    def test_bump_commits_with_chart_message(self):
        import api_commit
        self.prs = [listed(34, "komga")]
        commits = []
        real = api_commit.changed_files
        api_commit.changed_files = lambda wt: {"media-server/komga/Chart.yaml": b"x"}
        self.addCleanup(setattr, api_commit, "changed_files", real)
        self.run_it(commit=lambda *a: commits.append(a) or "oid")
        repo, branch, head, message, changes = commits[0]
        self.assertEqual((repo, head, message), ("o/r", "h34", "chore(komga): bump chart to 2.1.0+2"))
        self.assertEqual(branch, "dependabot/helm/media-server/komga/x")

    def test_major_hold_labels_and_requests_owner_review(self):
        self.prs = [listed(30, "sonarr")]
        self.run_it()
        self.assertIn(("pr", "edit", "30", "-R", "o/r", "--add-label", "major-update"), self.calls)
        self.assertIn(("pr", "edit", "30", "-R", "o/r", "--add-reviewer", "o"), self.calls)

    def test_fix_out_has_what_the_workflow_reads(self):
        self.prs = [listed(32, "frigate", labels=["ai-fix-attempt-1"]), listed(30, "sonarr")]
        self.check_state = "failure"
        self.run_it()
        fix = self.json.loads((Path(self.tmp.name) / "fix.json").read_text())
        self.assertEqual({k: fix[k] for k in ("number", "head_sha", "branch", "chart_dir", "attempts")},
                         dict(number=32, head_sha="h32", branch="dependabot/helm/media-server/frigate/x",
                              chart_dir="media-server/frigate", attempts=1))
        self.assertEqual(Path(fix["logs"]).read_text().count("LOGTEXT"), 2)
        self.assertEqual(Path(fix["logs"]).parent.name, "fix-logs")

    def test_one_broken_pr_does_not_stop_the_next(self):
        self.prs = [listed(34, "komga"), listed(32, "frigate")]
        real = S.chart_facts

        def flaky(head, base, files):
            if files[0].startswith("media-server/komga"):
                raise KeyError("boom")
            return real(head, base, files)

        S.chart_facts = flaky
        self.addCleanup(setattr, S, "chart_facts", real)
        self.run_it()
        self.assertIn("#34 error: KeyError", self.out.getvalue())
        self.assertIn("#32 merge", self.out.getvalue())


class CommitFixTests(unittest.TestCase):
    def setUp(self):
        import base64
        import io
        import json
        import subprocess
        self.b64, self.io, self.json = base64, io, json
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "media-server/radarr").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.repo / "media-server/radarr/values.yaml").write_text("a: 1\n")
        (self.repo / "tests/test_x.py").write_text("def test_a():\n    pass\n")
        git = ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True)
        self.sha = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        self.changes = self.root / "changes.json"
        self.gh_calls, self.commits = [], []

    def entry(self, path, content=None, **kw):
        base = dict(path=path, deleted=content is None, symlink=False,
                    content=None if content is None else self.b64.b64encode(content.encode()).decode())
        base.update(kw)
        return base

    def write(self, entries, outcome="success", raw=None):
        self.changes.write_text(raw if raw is not None else self.json.dumps(
            {"claude_outcome": outcome, "changes": entries}))

    def gh(self, *args):
        self.gh_calls.append(args)
        return ""

    def commit(self, *args):
        self.commits.append(args)
        return "newoid"

    def go(self, attempts=1):
        return S.commit_fix(self.changes, "o/r", 7, self.sha, "dependabot/helm/x", "media-server/radarr", attempts,
                            run=self.gh, commit=self.commit, repo_dir=self.repo, fetch=False, out=self.io.StringIO())

    def label(self, name):
        return ("pr", "edit", "7", "-R", "o/r", "--add-label", name)

    def test_clean_change_commits_at_expected_head_and_labels(self):
        self.write([self.entry("media-server/radarr/values.yaml", "a: 2\n")])
        self.go()
        self.assertEqual(len(self.commits), 1)
        repo, branch, expected_head, message, changes = self.commits[0]
        self.assertEqual((repo, branch, expected_head), ("o/r", "dependabot/helm/x", self.sha))
        self.assertEqual(changes, {"media-server/radarr/values.yaml": b"a: 2\n"})
        self.assertIn(self.label("ai-fix-attempt-2"), self.gh_calls)

    def test_guard_violation_flags_human_without_commit(self):
        self.write([self.entry("tests/test_x.py", ""), self.entry("evil.txt", "x")])
        self.go()
        self.assertEqual(self.commits, [])
        self.assertTrue(any(c[:2] == ("pr", "comment") for c in self.gh_calls))
        self.assertIn(self.label("needs-human"), self.gh_calls)

    def test_symlink_or_malformed_entry_is_needs_human(self):
        cases = [
            [self.entry("media-server/radarr/link", symlink=True, deleted=False)],
            [{"path": 5}],
            [{"path": "media-server/radarr/a", "deleted": False, "symlink": False, "content": "!!notbase64"}],
            ["not a dict"],
        ]
        for entries in cases:
            self.gh_calls.clear()
            self.write(entries)
            self.go()
            self.assertEqual(self.commits, [], entries)
            self.assertIn(self.label("needs-human"), self.gh_calls, entries)

    def test_malformed_file_is_needs_human_and_missing_file_counts_attempt(self):
        self.write(None, raw='{"changes": "x"}')
        self.go()
        self.assertIn(self.label("needs-human"), self.gh_calls)
        self.gh_calls.clear()
        self.changes.unlink()
        self.go()
        self.assertIn(self.label("ai-fix-attempt-2"), self.gh_calls)
        self.assertEqual(self.commits, [])

    def test_oversized_change_is_needs_human(self):
        self.write([self.entry("media-server/radarr/big.txt", "x" * (S.MAX_FILE_BYTES + 1))])
        self.go()
        self.assertEqual(self.commits, [])
        self.assertIn(self.label("needs-human"), self.gh_calls)
        self.gh_calls.clear()
        n = S.MAX_TOTAL_BYTES // S.MAX_FILE_BYTES + 1
        self.write([self.entry(f"media-server/radarr/f{i}.txt", "x" * S.MAX_FILE_BYTES) for i in range(n)])
        self.go()
        self.assertEqual(self.commits, [])
        self.assertIn(self.label("needs-human"), self.gh_calls)

    def test_commit_error_counts_an_attempt(self):
        self.write([self.entry("media-server/radarr/values.yaml", "a: 2\n")])

        def boom(*a):
            raise RuntimeError("graphql down")

        self.commit = boom
        self.go()
        self.assertIn(self.label("ai-fix-attempt-2"), self.gh_calls)

    def test_no_change_counts_an_attempt(self):
        self.write([])
        self.go()
        self.assertEqual(self.commits, [])
        self.assertIn(self.label("ai-fix-attempt-2"), self.gh_calls)

    def test_failed_claude_step_counts_and_third_attempt_flags_human(self):
        self.write([self.entry("media-server/radarr/values.yaml", "a: 2\n")], outcome="failure")
        self.go(attempts=2)
        self.assertEqual(self.commits, [])
        self.assertIn(self.label("ai-fix-attempt-3"), self.gh_calls)
        self.assertIn(self.label("needs-human"), self.gh_calls)
        self.assertEqual(sum(1 for c in self.gh_calls if c[:2] == ("pr", "comment")), 1)


if __name__ == "__main__":
    unittest.main()
