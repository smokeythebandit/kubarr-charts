"""The paths a chart declares are the paths it enforces.

`kubarr.storage.required` and `kubarr.storage.reserved` in a chart's values are
read by two consumers: the chart's own templates, which hand them to the common
storage validator, and the backend's install wizard
(api/src/services/install_options.rs), which builds the mount editor from them.
They used to live in a Rust table keyed by application name, which drifted --
radarr grew two reserved paths the table never learned, and alertmanager's
private claim at /alertmanager was reserved by nothing at all.

These tests keep the two readings in step: a declared path must really be
refused, a declared requirement must really be required, and a template may not
quietly carry a literal the values do not know about.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


# Fluent Bit shape-checks storage.mounts but never passes them to a pod: its
# DaemonSet mounts host paths directly. Its declaration is wizard metadata with
# no render to enforce it.
NO_MOUNT_ENFORCEMENT = ("monitoring/fluent-bit",)


def charts():
    return sorted(p.parent for p in ROOT.glob("*/*/Chart.yaml"))


def values_of(chart):
    path = chart / "values.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def declaration(values, kind, component):
    """What the backend's `declared()` would return for this component."""
    listed = (((values.get("kubarr") or {}).get("storage") or {}).get(kind) or {})
    if component in listed:
        return listed[component] or []
    return ["/config"] if kind == "reserved" else []


def declared_components(values):
    kubarr = (values.get("kubarr") or {}).get("storage") or {}
    return sorted({component for kind in ("required", "reserved")
                   for component in (kubarr.get(kind) or {})})


def render(chart, overrides=None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "values.yaml"
        path.write_text(yaml.safe_dump(overrides or {}))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", chart.name,
             str(chart_input(chart)), "-f", str(path)],
            capture_output=True, text=True,
        )
    return result


def mounts_for(values, component):
    storage = values.get("storage") or {}
    if component == "main":
        return storage.get("mounts")
    return ((storage.get("workloads") or {}).get(component) or {}).get("mounts")


def with_mounts(values, component, mounts):
    if component == "main":
        return {"storage": {"mounts": mounts}}
    return {"storage": {"workloads": {component: {"mounts": mounts}}}}


def probe_at(values, component, target):
    """The chart's own defaults plus one user mount on the path under test."""
    existing = mounts_for(values, component) or []
    probe = {"name": "contract-probe", "source": "contract/probe",
             "target": target, "mode": "read-write"}
    overrides = with_mounts(values, component, existing + [probe])
    # A chart that ships no shared-export mount of its own has no media claim
    # either; supply one so the probe fails on the path, not on the claim.
    if not ((values.get("storage") or {}).get("media") or {}).get("existingClaim"):
        overrides["storage"].setdefault("media", {})["existingClaim"] = "media-data"
    return overrides


def targetable(path):
    """The validator refuses dots in a mount target, so such a path can never
    be claimed by a user mount and cannot be probed for."""
    return path.startswith("/") and "." not in path


class InstallContractTests(unittest.TestCase):
    def test_components_named_in_the_declaration_exist(self):
        for chart in charts():
            values = values_of(chart)
            workloads = ((values.get("storage") or {}).get("workloads") or {})
            for component in declared_components(values):
                with self.subTest(chart=str(chart.relative_to(ROOT)), component=component):
                    self.assertTrue(
                        component == "main" or component in workloads,
                        "a declared component must be 'main' or a storage.workloads key")

    def test_no_template_carries_a_reserved_path_the_values_cannot_see(self):
        # A literal here is invisible to the wizard, which is how the Rust
        # table was allowed to disagree with the charts for so long.
        for chart in charts():
            if chart.name == "kubarr-common":
                continue  # the library defines the default the charts fall back to
            for template in sorted((chart / "templates").glob("**/*")):
                if not template.is_file():
                    continue
                body = template.read_text()
                for marker in ('"reservedPaths" (list', '"reservedPaths" (list)'):
                    with self.subTest(chart=str(chart.relative_to(ROOT)),
                                      template=template.name):
                        self.assertNotIn(
                            marker, body,
                            "pass .Values.kubarr.storage.reserved.<component> instead")

    def test_every_declared_reserved_path_is_refused(self):
        for chart in charts():
            relative = str(chart.relative_to(ROOT))
            if relative in NO_MOUNT_ENFORCEMENT:
                continue
            values = values_of(chart)
            for component in declared_components(values) or []:
                if mounts_for(values, component) is None:
                    continue  # the chart takes no user mounts for this workload
                for path in declaration(values, "reserved", component):
                    if not targetable(path):
                        continue
                    with self.subTest(chart=relative, component=component, path=path):
                        result = render(chart, probe_at(values, component, path))
                        self.assertNotEqual(
                            result.returncode, 0,
                            f"{path} is declared reserved but a user mount took it")
                        # The generic validator names the path it protected; a
                        # chart with a stricter rule of its own (immich's model
                        # cache) refuses first, in its own words.
                        if "overlaps" in result.stderr:
                            self.assertIn(path, result.stderr)

    def test_every_declared_requirement_is_enforced(self):
        for chart in charts():
            relative = str(chart.relative_to(ROOT))
            if relative in NO_MOUNT_ENFORCEMENT:
                continue
            values = values_of(chart)
            for component in declared_components(values) or []:
                required = declaration(values, "required", component)
                if not required or mounts_for(values, component) is None:
                    continue
                with self.subTest(chart=relative, component=component):
                    result = render(chart, with_mounts(values, component, []))
                    self.assertNotEqual(
                        result.returncode, 0,
                        f"{required} is declared required but an empty list rendered")

    def test_victorialogs_requirement_tracks_its_storage_path(self):
        # The chart exposes the same path twice: as the wizard's requirement and
        # as the flag it passes to the binary. They must not drift apart.
        values = values_of(ROOT / "monitoring/victorialogs")
        self.assertEqual(declaration(values, "required", "main"),
                         [values["victorialogs"]["storagePath"]])


if __name__ == "__main__":
    unittest.main()
