"""Sonarr and Radarr refresh Jellyfin's library through a key Kubarr shares.

Kubarr puts one API key in a Secret in each namespace and passes its name as
jellyfinIntegration.secretName. Jellyfin's sidecar registers the key in the
ApiKeys table; Sonarr and Radarr add a Jellyfin connection with it.
"""

import os
import sqlite3
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


SECRET = "kubarr-jellyfin-api-key"
KEY = "0123456789abcdef0123456789abcdef"
ARRS = ("media-manager/sonarr", "media-manager/radarr")
JELLYFIN = "media-server/jellyfin"


def render(chart, *sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(chart).name,
               str(chart_input(ROOT / chart))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def enabled(chart):
    return render(chart, f"jellyfinIntegration.secretName={SECRET}")


def without_plugin(chart=JELLYFIN):
    """The key sidecar runs only with the Kubarr plugin off; the plugin
    registers the key itself."""
    return render(chart, f"jellyfinIntegration.secretName={SECRET}", "kubarrPlugin.enabled=false")


def pod(resources, name):
    for doc in resources:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == name:
            return doc["spec"]["template"]["spec"]
    raise AssertionError(f"no Deployment {name}")


def container(spec, name):
    return next(c for c in spec["containers"] if c["name"] == name)


def link_policies(resources):
    return [d for d in resources if d["kind"] == "NetworkPolicy"
            and d["metadata"]["name"].endswith(("-library-refresh", "-jellyfin"))]


def secret_volumes(spec):
    return [v for v in spec.get("volumes", []) if v.get("secret", {}).get("secretName") == SECRET]


class JellyfinSidecarTests(unittest.TestCase):
    def test_nothing_is_added_without_a_key_from_kubarr(self):
        spec = pod(render(JELLYFIN), "jellyfin")
        self.assertEqual([c["name"] for c in spec["containers"]], ["jellyfin"])
        self.assertEqual(secret_volumes(spec), [])

    def test_sidecar_is_unprivileged_and_reads_the_key_read_only(self):
        spec = pod(without_plugin(), "jellyfin")
        sidecar = container(spec, "api-key")
        context = sidecar["securityContext"]
        self.assertIs(context["runAsNonRoot"], True)
        self.assertIs(context["allowPrivilegeEscalation"], False)
        self.assertIs(context["readOnlyRootFilesystem"], True)
        self.assertEqual(context["capabilities"]["drop"], ["ALL"])
        mounts = {m["mountPath"]: m for m in sidecar["volumeMounts"]}
        self.assertIs(mounts["/kubarr-secrets/jellyfin"].get("readOnly"), True)
        self.assertIn("/config", mounts)
        self.assertEqual(len(secret_volumes(spec)), 1)
        # The key stays out of Jellyfin's own container.
        jellyfin_mounts = [m["name"] for m in container(spec, "jellyfin")["volumeMounts"]]
        self.assertNotIn("api-key-secret", jellyfin_mounts)

    def test_sonarr_and_radarr_reach_jellyfin_only_while_linked(self):
        self.assertEqual(link_policies(render(JELLYFIN)), [])
        policies = link_policies(enabled(JELLYFIN))
        self.assertEqual(len(policies), 1)
        spec = policies[0]["spec"]
        self.assertEqual(spec["policyTypes"], ["Ingress"])
        sources = {peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                   for rule in spec["ingress"] for peer in rule["from"]}
        self.assertEqual(sources, {"sonarr", "radarr"})
        ports = {port["port"] for rule in spec["ingress"] for port in rule["ports"]}
        self.assertEqual(ports, {8096})


    def test_the_kubarr_plugin_replaces_the_sidecar(self):
        docs = enabled(JELLYFIN)
        spec = pod(docs, "jellyfin")
        self.assertNotIn("register-api-key", [c["name"] for c in spec["containers"]])
        self.assertFalse([d for d in docs if d["kind"] == "ConfigMap"
                          and d["metadata"]["name"].endswith("-api-key")])
        self.assertIn("install-kubarr-plugin", [c["name"] for c in spec["initContainers"]])


class ArrConnectionTests(unittest.TestCase):
    def test_key_is_mounted_only_when_kubarr_provides_one(self):
        for chart in ARRS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                self.assertEqual(secret_volumes(pod(render(chart), name)), [])
                spec = pod(enabled(chart), name)
                self.assertEqual(len(secret_volumes(spec)), 1)
                mounts = {m["mountPath"]: m for m in container(spec, name)["volumeMounts"]}
                self.assertIs(mounts["/kubarr-secrets/jellyfin"].get("readOnly"), True)
                self.assertIn("/custom-services.d/setup-mediaservers", mounts)

    def test_arrs_reach_jellyfin_only_while_linked(self):
        for chart in ARRS:
            with self.subTest(chart=chart):
                self.assertEqual(link_policies(render(chart)), [])
                policies = link_policies(enabled(chart))
                self.assertEqual(len(policies), 1)
                spec = policies[0]["spec"]
                self.assertEqual(spec["policyTypes"], ["Egress"])
                self.assertEqual(
                    spec["egress"],
                    [{"to": [{"namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "jellyfin"}}}],
                      "ports": [{"protocol": "TCP", "port": 8096}]}])

    def test_reused_values_from_an_older_release_still_render(self):
        # Kubarr updates with --reuse-values, which replaces the new chart's
        # defaults with the old release's values: jellyfinIntegration may be
        # absent entirely, or hold only the secretName Kubarr sets.
        for chart in ARRS + (JELLYFIN,):
            with self.subTest(chart=chart):
                self.assertEqual(link_policies(render(chart, "jellyfinIntegration=null")), [])

    def test_connection_script_is_valid_bash(self):
        for chart in ARRS:
            with self.subTest(chart=chart):
                config = next(d for d in render(chart)
                              if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-setup"))
                result = subprocess.run(["bash", "-n"], input=config["data"]["setup-mediaservers.sh"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class RegisterKeyTests(unittest.TestCase):
    """Run the sidecar's script against a database shaped like Jellyfin's."""

    def setUp(self):
        config = next(d for d in without_plugin()
                      if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-api-key"))
        self.module = types.ModuleType("register_api_key")
        exec(config["data"]["register-api-key.py"], self.module.__dict__)
        self.tmp = tempfile.TemporaryDirectory()
        self.module.DB = os.path.join(self.tmp.name, "jellyfin.db")

    def tearDown(self):
        self.tmp.cleanup()

    def create_jellyfin_db(self):
        db = sqlite3.connect(self.module.DB)
        db.execute(
            'CREATE TABLE "ApiKeys" ("Id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,'
            ' "DateCreated" TEXT NOT NULL, "DateLastActivity" TEXT NOT NULL,'
            ' "Name" TEXT NOT NULL, "AccessToken" TEXT NOT NULL)')
        db.commit()
        return db

    def rows(self, db):
        return db.execute("SELECT Name, AccessToken FROM ApiKeys ORDER BY Id").fetchall()

    def test_waits_for_jellyfin_to_create_its_database(self):
        self.assertIs(self.module.ensure(KEY), False)
        self.assertFalse(os.path.exists(self.module.DB), "must not create Jellyfin's database")
        sqlite3.connect(self.module.DB).close()  # created, migrations not run yet
        self.assertIs(self.module.ensure(KEY), False)

    def test_registers_once_and_restores_a_removed_key(self):
        db = self.create_jellyfin_db()
        db.execute("INSERT INTO ApiKeys (DateCreated, DateLastActivity, Name, AccessToken)"
                   " VALUES ('x', 'x', 'Mine', 'ffffffffffffffffffffffffffffffff')")
        db.commit()
        self.assertIs(self.module.ensure(KEY), True)
        self.assertIs(self.module.ensure(KEY), True)
        self.assertEqual(self.rows(db), [("Mine", "f" * 32), ("Kubarr", KEY)])
        db.execute("DELETE FROM ApiKeys WHERE Name = 'Kubarr'")
        db.commit()
        self.module.ensure(KEY)
        self.assertEqual(self.rows(db)[-1], ("Kubarr", KEY))

    def test_replaces_a_stale_kubarr_key(self):
        db = self.create_jellyfin_db()
        self.module.ensure("a" * 32)
        self.module.ensure(KEY)
        self.assertEqual(self.rows(db), [("Kubarr", KEY)])

    def test_rejects_a_malformed_key(self):
        path = os.path.join(self.tmp.name, "api-key")
        Path(path).write_text("not-a-key\n")
        self.module.KEY_FILE = path
        with self.assertRaises(ValueError):
            self.module.shared_key()
        Path(path).write_text(KEY + "\n")
        self.assertEqual(self.module.shared_key(), KEY)


if __name__ == "__main__":
    unittest.main()
