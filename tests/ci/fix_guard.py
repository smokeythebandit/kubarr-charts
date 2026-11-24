"""Guard for AI-made fixes: decide whether the changed files are allowed."""

import re

ALWAYS_ALLOWED = ("tests/chart_artifacts.py",)
TEST_FILE = re.compile(r"^tests/test_[^/]*\.py$")
FORBIDDEN_PREFIXES = (".github/", "tests/e2e/")
TEST_DEF = re.compile(r"\bdef test_")
SKIP = re.compile(r"\bskip\b|\bskipIf\b|\bexpectedFailure\b")


def _allowed_path(chart_dir, path):
    if path.startswith(FORBIDDEN_PREFIXES):
        return False
    return (path.startswith(chart_dir.rstrip("/") + "/")
            or path in ALWAYS_ALLOWED or bool(TEST_FILE.match(path)))


def check(chart_dir, changes, original):
    """Return violation messages for `changes` (empty list = allowed)."""
    violations = []
    for path, content in sorted(changes.items()):
        if not _allowed_path(chart_dir, path):
            violations.append(f"{path}: outside the paths an AI fix may change")
            continue
        before = original(path) or ""
        after = "" if content is None else content.decode("utf-8", "replace")
        if len(TEST_DEF.findall(after)) < len(TEST_DEF.findall(before)):
            violations.append(f"{path}: removes tests")
        if len(SKIP.findall(after)) > len(SKIP.findall(before)):
            violations.append(f"{path}: adds a skip or expectedFailure")
    return violations
