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

from chart_artifacts import DATABASE_CHARTS, ROOT, chart_input


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

    def test_a_mounted_managed_key_is_declared_on_both_sides(self):
        """Kubarr owns some API keys on an application's behalf: it generates
        the key, writes it into a Secret per namespace and passes only the
        Secret's name as <link>Integration.secretName. A chart that mounts such
        a key must therefore declare that value and reserve the mount path, or
        the key arrives nowhere and a user mount can shadow it."""
        for chart in charts():
            values = values_of(chart)
            for template in sorted((chart / "templates").glob("**/*")):
                if not template.is_file():
                    continue
                for line in template.read_text().splitlines():
                    marker = "/kubarr-secrets/"
                    if marker not in line:
                        continue
                    link = line.split(marker, 1)[1].split("/")[0].strip('"\' ')
                    if not link.isalpha():
                        continue
                    with self.subTest(chart=str(chart.relative_to(ROOT)), link=link):
                        integration = values.get(f"{link}Integration")
                        self.assertIsInstance(
                            integration, dict,
                            f"mounts a {link} key but declares no {link}Integration")
                        self.assertIn("secretName", integration)
                        self.assertEqual(
                            integration["secretName"], "",
                            "the chart must not name a Secret itself; Kubarr sets this")
                        self.assertIn(
                            f"{marker}{link}",
                            declaration(values, "reserved", "main"),
                            "a mounted key path must be reserved against user mounts")

    def test_the_platform_database_ships_no_password(self):
        """The platform database holds the user table, the password hashes,
        the JWT signing key and every managed API key. It shipped with
        `password: kubarr` in a public chart, and the installer never
        overrode it, so one published string opened every deployment. A chart
        that cannot render without being given a credential cannot regress to
        that."""
        for chart in charts():
            if chart.name not in DATABASE_CHARTS:
                continue
            values = values_of(chart)
            database = values.get("database") or {}
            with self.subTest(chart=str(chart.relative_to(ROOT))):
                self.assertEqual(database.get("password", ""), "",
                                 "a default password here is a published credential")
                self.assertEqual(database.get("existingSecret", ""), "")
                result = render(chart)
                self.assertNotEqual(result.returncode, 0,
                                    "rendered with no credential at all")
                self.assertIn("existingSecret", result.stderr,
                              "the refusal must say how to supply one")

    def test_every_shipped_container_is_bounded(self):
        """An unbounded pod competes with the applications for the node it
        shares. cert-manager's three workloads and the DNS webhook shipped
        with neither requests nor limits."""
        for chart in charts():
            if yaml.safe_load((chart / "Chart.yaml").read_text()).get("type") == "library":
                continue
            result = render(chart, {"database": {"existingSecret": "kubarr-db-app"}})
            if result.returncode:
                continue
            for doc in yaml.safe_load_all(result.stdout):
                if not doc or doc.get("kind") not in (
                        "Deployment", "StatefulSet", "DaemonSet", "CronJob"):
                    continue
                spec = (doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                        if doc["kind"] == "CronJob" else doc["spec"]["template"]["spec"])
                for container in spec["containers"]:
                    with self.subTest(chart=str(chart.relative_to(ROOT)),
                                      container=container["name"]):
                        resources = container.get("resources") or {}
                        self.assertTrue(resources.get("requests"), "no requests")
                        self.assertTrue(resources.get("limits"), "no limits")

    def test_no_claim_is_shared_by_replicas_that_cannot_all_attach_it(self):
        """More than one replica cannot share a ReadWriteOnce claim.

        This used to demand `Recreate` from any Deployment mounting such a
        claim, on the theory that a rolling update would wedge on the volume.
        The install gate disproved it: switching Grafana and the exportarr
        Deployments to Recreate made their upgrades fail outright, because a
        release published without a strategy has the API server's defaulted
        `rollingUpdate` recorded, and a server-side apply keeps it:

            Deployment.apps "grafana" is invalid: spec.strategy.rollingUpdate:
            Forbidden: may not be specified when strategy `type` is 'Recreate'

        Those rollouts were never stuck in the first place -- the claim's
        volume is node-bound and the exporters are pinned to their
        application's node, so the replacement pod lands where it can attach.
        What is always broken is two replicas sharing one single-writer claim,
        so that is what this asserts; whether a rollout actually completes is
        the gate's job, in a cluster.
        """
        for chart in charts():
            if yaml.safe_load((chart / "Chart.yaml").read_text()).get("type") == "library":
                continue
            result = render(chart, {"database": {"existingSecret": "kubarr-db-app"}})
            if result.returncode:
                continue
            docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            single_writer = {
                doc["metadata"]["name"] for doc in docs
                if doc.get("kind") == "PersistentVolumeClaim"
                and "ReadWriteOnce" in (doc["spec"].get("accessModes") or [])
            }
            for doc in docs:
                if doc.get("kind") != "Deployment":
                    continue
                mounted = {
                    volume["persistentVolumeClaim"]["claimName"]
                    for volume in doc["spec"]["template"]["spec"].get("volumes", [])
                    if volume.get("persistentVolumeClaim")
                } & single_writer
                if not mounted:
                    continue
                with self.subTest(chart=str(chart.relative_to(ROOT)),
                                  workload=doc["metadata"]["name"]):
                    self.assertLessEqual(
                        doc["spec"].get("replicas", 1), 1,
                        f"replicas share the single-writer claim {sorted(mounted)}")

    def test_no_claim_waits_on_a_pod_that_will_never_be_scheduled(self):
        """A claim needs a consumer, or the install waits for a bind that
        cannot happen.

        The cluster default StorageClass is typically WaitForFirstConsumer
        (local-path is), so a PersistentVolumeClaim stays Pending until a pod
        using it is scheduled. Recyclarr shipped a private claim with nothing
        but a suspended CronJob to consume it: no pod was ever created, the
        claim never bound, and `helm --wait` timed out and rolled the release
        back. A chart whose only workload is a CronJob must therefore not
        create a claim by default."""
        for chart in charts():
            if yaml.safe_load((chart / "Chart.yaml").read_text()).get("type") == "library":
                continue
            result = render(chart, {"database": {"existingSecret": "kubarr-db-app"}})
            if result.returncode:
                continue
            docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            kinds = {doc.get("kind") for doc in docs}
            # A claim pre-bound to a PersistentVolume by volumeName binds at
            # once; only a dynamically provisioned one waits for a consumer.
            claims = [doc for doc in docs if doc.get("kind") == "PersistentVolumeClaim"
                      and not doc["spec"].get("volumeName")]
            if not claims or kinds & {"Deployment", "StatefulSet", "DaemonSet"}:
                continue
            with self.subTest(chart=str(chart.relative_to(ROOT))):
                self.fail(
                    f"{[claim['metadata']['name'] for claim in claims]} has no pod to "
                    "bind it: the only workloads here are "
                    f"{sorted(kinds & {'CronJob', 'Job'})}")

    def test_a_chart_renders_without_the_values_it_added_this_release(self):
        """Kubarr upgrades with `helm upgrade --reuse-values`, which replays
        the old release's values and does not merge the new chart's defaults.
        A values key introduced in this release is therefore simply absent on
        the first upgrade, and a template that walks into it fails:

            at <.Values.kubarr.storage.reserved.main>:
            nil pointer evaluating interface {}.storage

        That broke the upgrade of every chart that gained the kubarr.storage
        block. Rendering with the block removed is what an upgrade from the
        previous release actually looks like."""
        for chart in charts():
            if yaml.safe_load((chart / "Chart.yaml").read_text()).get("type") == "library":
                continue
            overrides = {"kubarr": None, "database": {"existingSecret": "kubarr-db-app"}}
            with self.subTest(chart=str(chart.relative_to(ROOT))):
                result = render(chart, overrides)
                self.assertEqual(
                    result.returncode, 0,
                    "a chart must render when the values it added are absent:\n"
                    + result.stderr)

    def test_victorialogs_requirement_tracks_its_storage_path(self):
        # The chart exposes the same path twice: as the wizard's requirement and
        # as the flag it passes to the binary. They must not drift apart.
        values = values_of(ROOT / "monitoring/victorialogs")
        self.assertEqual(declaration(values, "required", "main"),
                         [values["victorialogs"]["storagePath"]])


if __name__ == "__main__":
    unittest.main()
