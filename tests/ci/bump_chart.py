#!/usr/bin/env python3
"""Derive a chart's appVersion/version from its primary image tag.

Dependabot only edits image tags in values.yaml, so chart versions never move
and nothing publishes. This rewrites the `version:` and `appVersion:` lines of
Chart.yaml (textually, keeping comments and key order) from the primary image.

    python3 tests/ci/bump_chart.py <chart_dir>...
    python3 tests/ci/bump_chart.py --all
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = "smokeythebandit/kubarr-charts"
ANNOTATION_PRIMARY = "kubarr.io/primary-image"
ANNOTATION_VERSIONING = "kubarr.io/versioning"


def normalize_tag(tag: str) -> str:
    """Image tag -> appVersion string (`3.09.00` -> `3.9.0`, `v3.10-ls122` -> `3.10`)."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", str(tag))
    if not match:
        raise ValueError(f"image tag has no leading version number: {tag!r}")
    return ".".join(str(int(part)) for part in match.group(1).split("."))


def version_core(app_version: str) -> str:
    """First three parts of appVersion, padded with 0 (`2.591` -> `2.591.0`)."""
    parts = app_version.split(".")[:3]
    parts += ["0"] * (3 - len(parts))
    return ".".join(parts)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _dig(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def _is_image(block) -> bool:
    return isinstance(block, dict) and "repository" in block and "tag" in block


def primary_image_path(chart_dir: Path) -> list[str]:
    chart = _load(Path(chart_dir) / "Chart.yaml")
    values = _load(Path(chart_dir) / "values.yaml")
    annotated = (chart.get("annotations") or {}).get(ANNOTATION_PRIMARY)
    if annotated:
        path = annotated.split(".")
        if not _is_image(_dig(values, path)):
            raise ValueError(f"{chart_dir}: annotation {annotated!r} is not an image block")
        return path
    for path in ([chart["name"], "image"], ["image"]):
        if _is_image(_dig(values, path)):
            return path
    raise ValueError(
        f"{chart_dir}: no primary image (set annotation {ANNOTATION_PRIMARY}, "
        f"or use '{chart['name']}.image' / 'image' in values.yaml)"
    )


def primary_tag(chart_dir: Path) -> str:
    values = _load(Path(chart_dir) / "values.yaml")
    return str(_dig(values, primary_image_path(chart_dir))["tag"])


def published_builds(chart_name: str, core: str) -> set[int]:
    """Build numbers already published for `chart_name` at `core` (e.g. 5.3.1).

    OCI tags spell `5.3.1+24` as `5.3.1_24`; a plain `5.3.1` counts as build 0.
    Raises on any registry error so the caller can fall back to a number that
    cannot collide.
    """
    path = f"{REGISTRY_PATH}/{chart_name}"
    with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{path}:pull", timeout=30) as response:
        token = json.load(response)["token"]
    builds, url = set(), f"https://ghcr.io/v2/{path}/tags/list?n=1000"
    while url:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            for tag in json.load(response).get("tags") or []:
                head, sep, build = tag.partition("_")
                if head == core and not sep:
                    builds.add(0)
                elif head == core and build.isdigit():
                    builds.add(int(build))
            link = response.headers.get("Link", "")
        match = re.search(r"<([^>]+)>", link)
        url = f"https://ghcr.io{match.group(1)}" if match else None
    return builds


def format_version(core: str, build: int) -> str:
    """`5.3.2` for build 0, `5.3.2+1` from the first chart-only change on."""
    return core if build == 0 else f"{core}+{build}"


def next_build(chart_name: str, core: str, current_build: int, published=None) -> int:
    """Build number for a chart moving to a new app version `core`.

    The build number counts chart revisions within one app version, so a new
    app version starts at 0 and is published as plain `5.3.2`. A core that was
    published before (going back to an older app version) continues after its
    highest published build. When the registry can't be asked, keep counting
    up from the current build: never reused, merely not reset.
    """
    if published is None:
        return 0
    try:
        seen = published(chart_name, core)
    except Exception:  # registry unreachable: fall back to a number that can't collide
        return current_build + 1
    return max(seen) + 1 if seen else 0


def bump(chart_dir: Path, revision_only_if_current: bool = False, published=None) -> bool:
    """Bump the chart to the primary image's version.

    When appVersion is already current this is a no-op, unless
    `revision_only_if_current` asks for just the build number to move
    (`1.2.3+4` -> `1.2.3+5`), which is what a sidecar-only change needs.

    A new app version drops the build number (`5.3.2`, see `next_build`);
    `published(chart_name, core) -> set[int]` is the registry lookup, None to
    skip it (tests).
    """
    chart_dir = Path(chart_dir)
    chart_file = chart_dir / "Chart.yaml"
    text = chart_file.read_text()
    chart = yaml.safe_load(text)
    app_version = normalize_tag(primary_tag(chart_dir))
    try:
        current = normalize_tag(str(chart.get("appVersion")))
    except ValueError:
        current = None
    build = re.search(r"\+(\d+)\s*$", str(chart.get("version", "")))
    if current == app_version:
        if not revision_only_if_current:
            return False
        core = str(chart.get("version", "")).split("+")[0]
        new_version = format_version(core, (int(build.group(1)) if build else 0) + 1)
        text, n_version = re.subn(r"^version:.*$", f"version: {new_version}", text, count=1, flags=re.M)
        if n_version != 1:
            raise ValueError(f"{chart_file}: missing top-level version line")
        chart_file.write_text(text)
        return True
    core = version_core(app_version)
    current_build = int(build.group(1)) if build else 0
    new_version = format_version(
        core, next_build(str(chart.get("name") or chart_dir.name), core, current_build, published))
    text, n_version = re.subn(r"^version:.*$", f"version: {new_version}", text, count=1, flags=re.M)
    text, n_app = re.subn(r"^appVersion:.*$", f'appVersion: "{app_version}"', text, count=1, flags=re.M)
    if n_version != 1 or n_app != 1:
        raise ValueError(f"{chart_file}: missing top-level version/appVersion line")
    chart_file.write_text(text)
    return True


def is_major(base_app_version: str, head_app_version: str, calver: bool) -> bool:
    if calver:
        return False
    return base_app_version.split(".")[0] != head_app_version.split(".")[0]


def application_charts(root: Path = ROOT) -> list[Path]:
    charts = []
    for chart_file in sorted(root.glob("*/*/Chart.yaml")):
        if _load(chart_file).get("type") == "application" and (chart_file.parent / "values.yaml").exists():
            charts.append(chart_file.parent)
    return charts


def main(argv: list[str]) -> int:
    if argv == ["--all"]:
        dirs = application_charts()
    else:
        dirs = [Path(a) for a in argv]
    for chart_dir in dirs:
        chart_file = chart_dir / "Chart.yaml"
        old = _load(chart_file).get("appVersion")
        try:
            changed = bump(chart_dir, published=published_builds)
        except ValueError as err:
            if argv == ["--all"]:
                continue  # wrapper charts without an image block are out of scope
            print(f"error {err}", file=sys.stderr)
            return 1
        if changed:
            print(f"bumped {chart_dir} {old} -> {_load(chart_file)['appVersion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
