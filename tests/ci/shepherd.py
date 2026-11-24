#!/usr/bin/env python3
"""Shepherd: decide the next step for each open Dependabot PR in kubarr-charts.

`decide` is pure; `chart_facts` only reads the PR's head and base trees.
`run` applies the decisions through `gh`; `commit-fix` commits an AI fix.
"""

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent))

import api_commit  # noqa: E402
import bump_chart  # noqa: E402
import fix_guard  # noqa: E402
import yaml  # noqa: E402

Action = Literal["ignore", "bump", "fix", "merge", "hold-major", "hold-platform", "needs-human", "wait"]

DEPENDABOT = "dependabot[bot]"
BRANCH_PREFIX = "dependabot/helm/"
REQUIRED_CHECKS = ("helm", "install")
PLATFORM_CATEGORIES = {"system", "monitoring"}
MAX_FIX_ATTEMPTS = 3
PENDING = {"in_progress", "queued", "pending", "waiting", "requested"}


@dataclass
class PullRequest:
    number: int
    head_ref: str
    head_sha: str
    base_sha: str
    author: str
    labels: set[str] = field(default_factory=set)
    files: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    conflicting: bool = False


@dataclass
class ChartFacts:
    chart_dir: str
    category: str
    stale: bool
    major: bool


def decide(pr: PullRequest, facts: ChartFacts | None) -> Action:
    if pr.author != DEPENDABOT or not pr.head_ref.startswith(BRANCH_PREFIX):
        return "ignore"
    if "needs-human" in pr.labels:
        return "ignore"
    if pr.conflicting or facts is None:
        return "needs-human"
    if facts.stale:
        return "bump"
    states = [pr.checks.get(name) for name in REQUIRED_CHECKS]
    if any(s is not None and s not in PENDING and s != "success" for s in states):
        attempts = sum(1 for label in pr.labels if label.startswith("ai-fix-attempt-"))
        return "fix" if attempts < MAX_FIX_ATTEMPTS else "needs-human"
    if any(s != "success" for s in states):
        return "wait"
    if facts.category in PLATFORM_CATEGORIES:
        return "ignore" if "platform-update" in pr.labels else "hold-platform"
    if facts.major:
        return "ignore" if "major-update" in pr.labels else "hold-major"
    return "merge"


def _tag_at(root: Path, path: list[str]) -> str:
    values = yaml.safe_load((root / "values.yaml").read_text()) or {}
    for key in path:
        values = values[key]
    return bump_chart.normalize_tag(values["tag"])


def _annotations(chart_dir: Path) -> dict:
    chart = yaml.safe_load((chart_dir / "Chart.yaml").read_text()) or {}
    annotations = chart.get("annotations") or {}
    return {key: annotations.get(key) for key in (bump_chart.ANNOTATION_VERSIONING, bump_chart.ANNOTATION_PRIMARY)}


def chart_facts(repo_root: Path, base_root: Path, files: list[str]) -> ChartFacts | None:
    """What the PR does to its chart, judged by the BASE's rules.

    The calver flag and the primary image path come from the base chart, and a
    PR that changes them (or that has no readable tags) gets None, so an edit to
    the PR's own metadata can never switch off the major hold.
    """
    dirs = set()
    for name in files:
        parts = Path(name).parts
        if len(parts) < 3:
            return None
        dirs.add(parts[:2])
    if len(dirs) != 1:
        return None
    category, name = next(iter(dirs))
    chart_dir = f"{category}/{name}"
    head, base = Path(repo_root) / chart_dir, Path(base_root) / chart_dir
    if not (head / "Chart.yaml").exists() or not (base / "Chart.yaml").exists():
        return None  # fail closed: cannot tell whether this is a major bump
    try:
        if _annotations(head) != _annotations(base):
            return None
        path = bump_chart.primary_image_path(base)
        base_tag, head_tag = _tag_at(base, path), _tag_at(head, path)
        base_chart = yaml.safe_load((base / "Chart.yaml").read_text()) or {}
        head_chart = yaml.safe_load((head / "Chart.yaml").read_text()) or {}
        calver = _annotations(base)[bump_chart.ANNOTATION_VERSIONING] == "calver"
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError):
        return None
    try:
        app_current = bump_chart.normalize_tag(str(head_chart.get("appVersion"))) == head_tag
    except ValueError:
        app_current = False
    # The PR needs a bump when it left the chart version alone (a sidecar-only
    # change) or when appVersion lags the primary tag.
    stale = str(head_chart.get("version")) == str(base_chart.get("version")) or not app_current
    major = bump_chart.is_major(base_tag, head_tag, calver)
    return ChartFacts(chart_dir=chart_dir, category=category, stale=stale, major=major)


LOG_LIMIT = 60 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
BOT_LOGINS = {"dependabot", "app/dependabot", DEPENDABOT}


def gh(*args: str) -> str:
    """Run `gh` (authenticated by GH_TOKEN) and return stdout; raise on failure."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout


def git(*args: str, cwd=None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def materialize(sha: str, parent: Path, name: str) -> Path:
    """Check out `sha` as a detached worktree under `parent`."""
    path = Path(parent) / name
    git("fetch", "--no-tags", "origin", sha)
    git("worktree", "add", "--detach", str(path), sha)
    return path


def check_states(run, repo: str, sha: str) -> tuple[dict, dict]:
    """Required check name -> state, and name -> run id, from the newest GitHub Actions run of each name.

    Older runs of the same name (re-runs) are ignored; the state is the conclusion,
    or the status while unfinished.
    """
    out = run("api", f"repos/{repo}/commits/{sha}/check-runs", "--paginate",
              "--jq", ".check_runs[] | {id, name, status, conclusion, url: .details_url, app: .app.slug}")
    latest = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item["name"] not in REQUIRED_CHECKS or item.get("app") != "github-actions":
            continue
        if item["name"] not in latest or item["id"] > latest[item["name"]]["id"]:
            latest[item["name"]] = item
    states, runs = {}, {}
    for name, item in latest.items():
        done = item["status"] == "completed"
        states[name] = (item["conclusion"] or "failure") if done else item["status"]
        found = re.search(r"/actions/runs/(\d+)", item.get("url") or "")
        if found:
            runs[name] = found.group(1)
    return states, runs


def failed_logs(run, repo: str, states: dict, runs: dict) -> str:
    parts = []
    for name in REQUIRED_CHECKS:
        state = states.get(name)
        if state is None or state in PENDING or state == "success" or name not in runs:
            continue
        try:
            text = run("run", "view", runs[name], "-R", repo, "--log-failed")
        except RuntimeError as exc:
            text = f"(could not fetch logs: {exc})"
        parts.append(f"===== check '{name}' ({state}) =====\n{text}")
    return "\n".join(parts).encode()[-LOG_LIMIT:].decode("utf-8", "replace")


def add_label(run, repo, number, label):
    run("pr", "edit", str(number), "-R", repo, "--add-label", label)


def hold(run, repo, number, label):
    # Review first: the label makes later runs ignore the PR, so it goes last.
    run("pr", "edit", str(number), "-R", repo, "--add-reviewer", repo.split("/")[0])
    add_label(run, repo, number, label)


def flag_human(run, repo, number, body):
    add_label(run, repo, number, "needs-human")
    run("pr", "comment", str(number), "-R", repo, "--body", body)


def load_prs(run, repo):
    out = run("pr", "list", "-R", repo, "--state", "open", "--author", "app/dependabot", "--limit", "100",
              "--json", "number,headRefName,headRefOid,baseRefOid,labels,files,author,mergeable")
    return json.loads(out or "[]")


def run_shepherd(repo, fix_out, dry_run=False, run=gh, checkout=materialize, commit=api_commit.commit_files, out=None):
    out = out or sys.stdout
    work = Path(tempfile.mkdtemp(prefix="shepherd-", dir=os.environ.get("RUNNER_TEMP")))
    fix_written = False
    for item in load_prs(run, repo):
        number = item["number"]
        login = (item.get("author") or {}).get("login", "")
        pr = PullRequest(
            number=number, head_ref=item["headRefName"], head_sha=item["headRefOid"],
            base_sha=item["baseRefOid"], author=DEPENDABOT if login in BOT_LOGINS else login,
            labels={label["name"] for label in item.get("labels", [])},
            files=[f["path"] for f in item.get("files", [])],
            conflicting=item.get("mergeable") == "CONFLICTING",
        )
        if not pr.head_ref.startswith(BRANCH_PREFIX) or "needs-human" in pr.labels:
            print(f"#{number} ignore", file=out)
            continue
        try:
            if pr.conflicting:
                head, facts, runs = None, None, {}
            else:
                pr.checks, runs = check_states(run, repo, pr.head_sha)
                head = checkout(pr.head_sha, work, f"head-{number}")
                base = checkout(pr.base_sha, work, f"base-{number}")
                facts = chart_facts(head, base, pr.files)
            action = decide(pr, facts)
            print(f"#{number} {action}", file=out)
            if dry_run or action in ("ignore", "wait"):
                continue
            if action == "bump":
                chart = head / facts.chart_dir
                if not bump_chart.bump(chart, revision_only_if_current=True):
                    print(f"#{number} nothing to bump", file=out)
                    continue
                version = yaml.safe_load((chart / "Chart.yaml").read_text())["version"]
                oid = commit(repo, pr.head_ref, pr.head_sha, f"chore({chart.name}): bump chart to {version}",
                             api_commit.changed_files(head))
                print(f"#{number} " + (f"committed {oid}" if oid else "head moved, skipped"), file=out)
            elif action == "merge":
                run("pr", "merge", str(number), "-R", repo, "--merge", "--match-head-commit", pr.head_sha)
            elif action == "hold-major":
                hold(run, repo, number, "major-update")
            elif action == "hold-platform":
                hold(run, repo, number, "platform-update")
            elif action == "needs-human":
                reason = ("it conflicts with the base branch. Comment `@dependabot recreate` on this PR so it is "
                          "rebuilt; the shepherd will then bump the new PR"
                          if pr.conflicting else
                          "it touches more than one chart or a non-chart path, changes the chart's version "
                          "metadata, or its base chart is unreadable"
                          if facts is None else f"{MAX_FIX_ATTEMPTS} automatic fix attempts did not make CI green")
                flag_human(run, repo, number, f"Shepherd needs a human here: {reason}.")
            elif action == "fix" and not fix_written:
                logs_dir = Path(fix_out).parent / "fix-logs"
                logs_dir.mkdir(exist_ok=True)
                logs = logs_dir / f"logs-{number}.txt"
                logs.write_text(failed_logs(run, repo, pr.checks, runs))
                attempts = sum(1 for label in pr.labels if label.startswith("ai-fix-attempt-"))
                Path(fix_out).write_text(json.dumps({
                    "number": number, "head_sha": pr.head_sha, "chart_dir": facts.chart_dir,
                    "logs": str(logs), "repo": repo,
                    "branch": pr.head_ref, "attempts": attempts,
                }))
                fix_written = True
        except Exception as exc:  # one broken PR must not stop the others
            print(f"#{number} error: {type(exc).__name__}: {exc}", file=out)
    return 0


def count_attempt(run, repo, number, attempts, out):
    """Record a fix attempt that produced no commit; the third one hands the PR to a human."""
    add_label(run, repo, number, f"ai-fix-attempt-{attempts + 1}")
    if attempts + 1 >= MAX_FIX_ATTEMPTS:
        flag_human(run, repo, number, f"Shepherd gave up after {MAX_FIX_ATTEMPTS} automatic fix attempts.")
    print(f"#{number} attempt {attempts + 1} counted", file=out)


def parse_changes(path):
    """Read the untrusted change set written by the fix job.

    Returns (claude_outcome, changes, problems). Anything malformed is a problem,
    never an exception; symlink entries become SymlinkChange so the guard rejects them.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return "failure", {}, []  # no usable result: the fix job did not finish
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
        return "failure", {}, ["changes.json is malformed"]
    outcome = data.get("claude_outcome")
    outcome = outcome if isinstance(outcome, str) else "failure"
    changes, problems = {}, []
    for entry in data["changes"]:
        try:
            path_ = entry["path"]
            if not isinstance(path_, str) or not path_ or "\0" in path_:
                raise ValueError("bad path")
            if entry.get("symlink") is True:
                changes[path_] = api_commit.SymlinkChange()
            elif entry.get("deleted") is True:
                changes[path_] = None
            else:
                content = entry["content"]
                if len(content) > MAX_FILE_BYTES * 4 // 3 + 4:
                    raise ValueError("too large")
                changes[path_] = base64.b64decode(content, validate=True)
                if len(changes[path_]) > MAX_FILE_BYTES:
                    raise ValueError("too large")
        except (KeyError, TypeError, ValueError, AttributeError, binascii.Error):
            problems.append(f"malformed change entry: {str(entry)[:80]}")
    if sum(len(c) for c in changes.values() if c) > MAX_TOTAL_BYTES:
        problems.append("the change set is larger than the allowed total size")
    return outcome, changes, problems


def commit_fix(changes_path, repo, number, head_sha, branch, chart_dir, attempts, run=gh,
               commit=api_commit.commit_files, repo_dir=".", fetch=True, out=None):
    """Commit the AI fix described by the (untrusted) `changes_path` onto the PR branch."""
    out = out or sys.stdout
    claude_outcome, changes, problems = parse_changes(changes_path)
    if problems:
        flag_human(run, repo, number, "The AI fix was rejected, nothing was committed:\n- " + "\n- ".join(problems))
        print(f"#{number} fix rejected: {problems}", file=out)
        return 0
    if claude_outcome != "success" or not changes:
        count_attempt(run, repo, number, attempts, out)
        return 0
    if fetch:
        git("fetch", "--no-tags", "origin", head_sha, cwd=repo_dir)

    def original(path):
        proc = subprocess.run(["git", "-C", str(repo_dir), "show", f"{head_sha}:{path}"], capture_output=True)
        return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None

    violations = fix_guard.check(chart_dir, changes, original)
    if violations:
        flag_human(run, repo, number, "The AI fix was rejected, nothing was committed:\n- " + "\n- ".join(violations))
        print(f"#{number} fix rejected: {violations}", file=out)
        return 0
    chart = Path(chart_dir).name
    try:
        oid = commit(repo, branch, head_sha, f"fix({chart}): repair CI after image bump (AI)", changes)
    except Exception as exc:  # a commit that keeps failing must not be retried forever
        print(f"#{number} commit failed: {type(exc).__name__}: {str(exc)[:200]}", file=out)
        count_attempt(run, repo, number, attempts, out)
        return 0
    if oid is None:
        print(f"#{number} head moved, fix skipped", file=out)
        return 0
    add_label(run, repo, number, f"ai-fix-attempt-{attempts + 1}")
    print(f"#{number} committed {oid}", file=out)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--repo", required=True)
    p_run.add_argument("--fix-out", required=True)
    p_run.add_argument("--dry-run", action="store_true")
    p_fix = sub.add_parser("commit-fix")
    p_fix.add_argument("--repo", required=True)
    p_fix.add_argument("--changes", required=True)
    p_fix.add_argument("--pr", type=int, required=True)
    p_fix.add_argument("--head-sha", required=True)
    p_fix.add_argument("--branch", required=True)
    p_fix.add_argument("--chart-dir", required=True)
    p_fix.add_argument("--attempts", type=int, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run_shepherd(args.repo, args.fix_out, args.dry_run)
    return commit_fix(args.changes, args.repo, args.pr, args.head_sha, args.branch, args.chart_dir, args.attempts)


if __name__ == "__main__":
    sys.exit(main())
