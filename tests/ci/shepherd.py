#!/usr/bin/env python3
"""Shepherd: decide the next step for each open Dependabot PR in kubarr-charts.

`decide` is pure; `chart_facts` only reads the PR's head and base trees.
`run` applies the decisions through `gh`; `commit-fix` commits an AI fix.
"""

import argparse
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
    if facts is None:
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


def chart_facts(repo_root: Path, base_root: Path, files: list[str]) -> ChartFacts | None:
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
    if not (head / "Chart.yaml").exists():
        return None
    chart = yaml.safe_load((head / "Chart.yaml").read_text()) or {}
    head_tag = bump_chart.normalize_tag(bump_chart.primary_tag(head))
    stale = str(chart.get("appVersion")) != head_tag
    calver = (chart.get("annotations") or {}).get(bump_chart.ANNOTATION_VERSIONING) == "calver"
    if not (base / "Chart.yaml").exists():
        return None  # fail closed: cannot tell whether this is a major bump
    try:
        base_tag = bump_chart.normalize_tag(bump_chart.primary_tag(base))
    except (ValueError, KeyError, TypeError):
        return None
    major = bump_chart.is_major(base_tag, head_tag, calver)
    return ChartFacts(chart_dir=chart_dir, category=category, stale=stale, major=major)


LOG_LIMIT = 60 * 1024
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
    """Required check name -> state (conclusion, or status while unfinished), and name -> run id."""
    out = run("api", f"repos/{repo}/commits/{sha}/check-runs", "--paginate",
              "--jq", ".check_runs[] | {name, status, conclusion, url: .details_url}")
    states, runs = {}, {}
    for line in out.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item["name"] not in REQUIRED_CHECKS:
            continue
        done = item["status"] == "completed"
        states[item["name"]] = (item["conclusion"] or "failure") if done else item["status"]
        found = re.search(r"/actions/runs/(\d+)", item.get("url") or "")
        if found:
            runs[item["name"]] = found.group(1)
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
    add_label(run, repo, number, label)
    run("pr", "edit", str(number), "-R", repo, "--add-reviewer", repo.split("/")[0])


def flag_human(run, repo, number, body):
    add_label(run, repo, number, "needs-human")
    run("pr", "comment", str(number), "-R", repo, "--body", body)


def load_prs(run, repo):
    out = run("pr", "list", "-R", repo, "--state", "open", "--author", "app/dependabot", "--limit", "100",
              "--json", "number,headRefName,headRefOid,baseRefOid,labels,files,author")
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
        )
        if not pr.head_ref.startswith(BRANCH_PREFIX) or "needs-human" in pr.labels:
            print(f"#{number} ignore", file=out)
            continue
        try:
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
                if not bump_chart.bump(chart):
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
                reason = ("it touches more than one chart or a non-chart path, or its base chart is unreadable"
                          if facts is None else f"{MAX_FIX_ATTEMPTS} automatic fix attempts did not make CI green")
                flag_human(run, repo, number, f"Shepherd needs a human here: {reason}.")
            elif action == "fix" and not fix_written:
                logs = work / f"logs-{number}.txt"
                logs.write_text(failed_logs(run, repo, pr.checks, runs))
                attempts = sum(1 for label in pr.labels if label.startswith("ai-fix-attempt-"))
                Path(fix_out).write_text(json.dumps({
                    "number": number, "head_sha": pr.head_sha, "chart_dir": facts.chart_dir,
                    "worktree": str(head), "logs": str(logs), "repo": repo,
                    "branch": pr.head_ref, "attempts": attempts,
                }))
                fix_written = True
        except (RuntimeError, subprocess.CalledProcessError, api_commit.GraphQLError, OSError, ValueError) as exc:
            print(f"#{number} error: {exc}", file=out)
    return 0


def commit_fix(fix_in, run=gh, commit=api_commit.commit_files, out=None):
    out = out or sys.stdout
    fix = json.loads(Path(fix_in).read_text())
    worktree, repo, number = Path(fix["worktree"]), fix["repo"], fix["number"]
    changes = api_commit.changed_files(worktree)
    if not changes:
        print(f"#{number} the fix changed nothing; attempt not counted", file=out)
        return 0

    def original(path):
        proc = subprocess.run(["git", "-C", str(worktree), "show", f"HEAD:{path}"], capture_output=True)
        return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None

    violations = fix_guard.check(fix["chart_dir"], changes, original)
    if violations:
        flag_human(run, repo, number, "The AI fix was rejected, nothing was committed:\n- " + "\n- ".join(violations))
        print(f"#{number} fix rejected: {violations}", file=out)
        return 0
    chart = Path(fix["chart_dir"]).name
    oid = commit(repo, fix["branch"], fix["head_sha"], f"fix({chart}): repair CI after image bump (AI)", changes)
    if oid is None:
        print(f"#{number} head moved, fix skipped", file=out)
        return 0
    add_label(run, repo, number, f"ai-fix-attempt-{fix['attempts'] + 1}")
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
    p_fix.add_argument("--fix-in", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run_shepherd(args.repo, args.fix_out, args.dry_run)
    return commit_fix(args.fix_in)


if __name__ == "__main__":
    sys.exit(main())
