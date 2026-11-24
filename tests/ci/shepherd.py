#!/usr/bin/env python3
"""Shepherd: decide the next step for each open Dependabot PR in kubarr-charts.

`decide` is pure; `chart_facts` only reads the PR's head and base trees.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent))

import bump_chart  # noqa: E402
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
    major = False
    if (base / "Chart.yaml").exists():
        base_tag = bump_chart.normalize_tag(bump_chart.primary_tag(base))
        major = bump_chart.is_major(base_tag, head_tag, calver)
    return ChartFacts(chart_dir=chart_dir, category=category, stale=stale, major=major)
