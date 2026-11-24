"""Signed API commits and the guard on AI-made fixes (chart shepherd)."""

import base64
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


api_commit = load("api_commit")
fix_guard = load("fix_guard")


class ApiCommit(unittest.TestCase):
    def test_commit_builds_signed_api_call(self):
        calls = []
        oid = api_commit.commit_files(
            "o/r", "dependabot/helm/x", "abc", "fix: x",
            {"media-server/komga/values.yaml": b"a: 1\n", "old.txt": None},
            runner=lambda q, v: calls.append(v) or {"data": {"createCommitOnBranch": {"commit": {"oid": "def"}}}})
        self.assertEqual(oid, "def")
        vars_ = calls[0]["input"]
        self.assertEqual(vars_["expectedHeadOid"], "abc")
        self.assertEqual(vars_["branch"], {"repositoryNameWithOwner": "o/r", "branchName": "dependabot/helm/x"})
        self.assertEqual(vars_["message"], {"headline": "fix: x"})
        self.assertEqual(vars_["fileChanges"]["deletions"], [{"path": "old.txt"}])
        self.assertEqual(vars_["fileChanges"]["additions"],
                         [{"path": "media-server/komga/values.yaml",
                           "contents": base64.b64encode(b"a: 1\n").decode()}])

    def test_commit_conflict_is_skip(self):
        def runner(q, v):
            raise api_commit.GraphQLError("Expected branch to point to \"abc\" but it did not")
        self.assertIsNone(api_commit.commit_files("o/r", "b", "abc", "m", {"f": b""}, runner=runner))

    def test_other_errors_raise(self):
        def runner(q, v):
            raise api_commit.GraphQLError("Resource not accessible")
        with self.assertRaises(api_commit.GraphQLError):
            api_commit.commit_files("o/r", "b", "abc", "m", {"f": b""}, runner=runner)

    def test_multiline_message_splits_headline_and_body(self):
        calls = []
        api_commit.commit_files("o/r", "b", "abc", "head\n\nbody text", {"f": b""},
                                runner=lambda q, v: calls.append(v) or {"data": {"createCommitOnBranch": {"commit": {"oid": "d"}}}})
        self.assertEqual(calls[0]["input"]["message"], {"headline": "head", "body": "body text"})

    def test_changed_files_reads_worktree_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            git = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            git("config", "commit.gpgsign", "false")
            (wt / "keep.txt").write_text("1")
            (wt / "gone.txt").write_text("1")
            (wt / "mv.txt").write_text("1")
            git("add", ".")
            git("commit", "-qm", "init")
            (wt / "keep.txt").write_text("2")
            (wt / "gone.txt").unlink()
            git("mv", "mv.txt", "moved.txt")
            (wt / "sub").mkdir()
            (wt / "sub" / "new file.txt").write_bytes(b"n")
            self.assertEqual(api_commit.changed_files(wt), {
                "keep.txt": b"2", "gone.txt": None, "mv.txt": None,
                "moved.txt": b"1", "sub/new file.txt": b"n"})


class FixGuard(unittest.TestCase):
    def test_guard_allows_chart_and_tests(self):
        self.assertEqual(fix_guard.check(
            "media-server/komga",
            {"media-server/komga/values.yaml": b"x",
             "tests/test_komga.py": b"def test_a(): pass\ndef test_b(): pass\n"},
            original=lambda p: "def test_a(): pass\n" if p.endswith(".py") else ""), [])

    def test_guard_rejects_workflow_and_e2e(self):
        v = fix_guard.check("media-server/komga",
                            {".github/workflows/ci.yml": b"", "tests/e2e/install_gate.py": b""},
                            original=lambda p: "")
        self.assertEqual(len(v), 2)

    def test_guard_rejects_other_chart(self):
        self.assertTrue(fix_guard.check("media-server/komga", {"media-server/plex/values.yaml": b""},
                                        original=lambda p: ""))

    def test_guard_rejects_sibling_with_chart_prefix(self):
        self.assertTrue(fix_guard.check("media-server/komga", {"media-server/komga-extra/values.yaml": b""},
                                        original=lambda p: ""))

    def test_guard_rejects_removed_or_skipped_tests(self):
        self.assertTrue(fix_guard.check("media-server/komga", {"tests/test_komga.py": b"def test_a(): pass\n"},
                                        original=lambda p: "def test_a(): pass\ndef test_b(): pass\n"))
        self.assertTrue(fix_guard.check("media-server/komga",
                                        {"tests/test_komga.py": b"@unittest.skip('x')\ndef test_a(): pass\n"},
                                        original=lambda p: "def test_a(): pass\n"))

    def test_guard_rejects_deleted_test_file_and_other_skips(self):
        self.assertTrue(fix_guard.check("media-server/komga", {"tests/test_komga.py": None},
                                        original=lambda p: "def test_a(): pass\n"))
        for marker in ("@unittest.skipIf(True, 'x')", "@unittest.expectedFailure"):
            self.assertTrue(fix_guard.check("media-server/komga",
                                            {"tests/test_komga.py": (marker + "\ndef test_a(): pass\n").encode()},
                                            original=lambda p: "def test_a(): pass\n"))

    def test_guard_allows_preexisting_skip(self):
        src = "@unittest.skip('x')\ndef test_a(): pass\n"
        self.assertEqual(fix_guard.check("media-server/komga", {"tests/test_komga.py": src.encode()},
                                         original=lambda p: src), [])


if __name__ == "__main__":
    unittest.main()
