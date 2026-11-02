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


# The platform database charts ship no default password: supplying one is the
# installer's job, and the Kubarr CLI creates the Secret these names refer to.
# A bare `helm template` of them is therefore expected to fail, so a test that
# is examining something else has to hand them a credential first.
DATABASE_CHARTS = ("postgresql", "kubarr-backend", "kubarr-worker")


def credential_args(chart):
    """`--set` arguments a chart needs before it will render at all."""
    name = chart if isinstance(chart, str) else Path(chart).name
    if name in DATABASE_CHARTS:
        return ["--set", "database.existingSecret=kubarr-db-app"]
    return []
