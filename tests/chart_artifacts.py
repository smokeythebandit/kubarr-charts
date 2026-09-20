"""Select source charts locally or the exact CI packages when configured."""

import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def chart_input(source):
    directory = os.environ.get("CHART_PACKAGES_DIR")
    if directory is None:
        return source
    metadata = yaml.safe_load((source / "Chart.yaml").read_text())
    package = Path(directory) / f"{metadata['name']}-{metadata['version']}.tgz"
    if not package.is_file():
        raise FileNotFoundError(f"Required chart package missing: {package}")
    return package
