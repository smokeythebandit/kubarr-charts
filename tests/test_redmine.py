"""Render tests for Redmine storage, database, gateway, and secret references."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "development/redmine"


def render(overrides=None, release="redmine", upgrade=False):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        values.write_text(yaml.safe_dump(overrides or {}))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", release,
             str(chart_input(CHART)), "-f", str(values),
             *(["--is-upgrade"] if upgrade else [])],
            text=True, capture_output=True,
        )
    return result, ([r for r in yaml.safe_load_all(result.stdout) if r]
                    if result.returncode == 0 else [])


def objects(resources, kind):
    return {r["metadata"]["name"]: r for r in resources if r["kind"] == kind}


def container(workload):
    return workload["spec"]["template"]["spec"]["containers"][0]


def env(workload):
    return {item["name"]: item for item in container(workload)["env"]}


class RedmineTests(unittest.TestCase):
    def test_preinstall_guard_inspects_only_redmine_database_path(self):
        hook = (CHART / "templates/check-existing-database.yaml").read_text()
        self.assertIn('"helm.sh/hook": pre-install', hook)
        self.assertIn('claimName: {{ .Values.storage.media.existingClaim | quote }}', hook)
        self.assertIn('readOnly: true', hook)
        self.assertIn('(not $secret) $claim', hook)
        match = re.search(r'^            - \|\n((?:              .*\n)+)', hook, re.M)
        self.assertIsNotNone(match)
        script = textwrap.dedent(match.group(1))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "development/redmine/redmine/postgres"

            def check():
                return subprocess.run(["sh", "-ec", script, "--", str(db)],
                                      capture_output=True, text=True)

            self.assertEqual(check().returncode, 0, "an unrelated existing share is not a database")
            db.mkdir(parents=True)
            self.assertEqual(check().returncode, 0, "an empty database directory permits first install")
            (db / ".hidden").touch()
            self.assertNotEqual(check().returncode, 0, "hidden files are database data too")
            (db / ".hidden").unlink()
            (db / "\n").touch()
            self.assertNotEqual(check().returncode, 0, "newlines must not mask a directory entry")
            (db / "\n").unlink()
            (db / "pgdata").mkdir()
            (db / "pgdata/PG_VERSION").write_text("16")
            self.assertNotEqual(check().returncode, 0, "existing PostgreSQL data must block new credentials")
            (db / "pgdata/PG_VERSION").unlink()
            (db / "pgdata").rmdir()
            db.rmdir()
            db.symlink_to(root)
            self.assertNotEqual(check().returncode, 0, "a symlink must not bypass the check")

    def test_default_bundled_install(self):
        result, resources = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.assertEqual(chart["annotations"]["kubarr.io/category"], "development")
        app = objects(resources, "Deployment")["redmine"]
        db = objects(resources, "StatefulSet")["redmine-postgres"]
        services = objects(resources, "Service")
        self.assertEqual(services["redmine"]["spec"]["ports"][0]["port"], 3000)
        self.assertEqual(services["redmine"]["spec"]["selector"],
                         app["spec"]["template"]["metadata"]["labels"])
        self.assertEqual(services["redmine-postgres"]["spec"]["selector"],
                         db["spec"]["template"]["metadata"]["labels"])
        self.assertEqual(app["spec"]["strategy"]["type"], "Recreate")
        self.assertEqual(app["spec"]["replicas"], 1)
        self.assertEqual(env(app)["REDMINE_DB_POSTGRES"]["value"], "redmine-postgres")
        init = app["spec"]["template"]["spec"]["initContainers"][-1]
        self.assertEqual(init["command"], ["sh", "-c", "until pg_isready; do sleep 3; done"])
        self.assertEqual({item["name"]: item["value"] for item in init["env"]}["PGHOST"], "redmine-postgres")
        self.assertEqual(env(app)["REDMINE_DB_PORT"]["value"], "5432")
        self.assertEqual(env(app)["REDMINE_DB_DATABASE"]["value"], "redmine")
        self.assertEqual(container(app)["ports"][0]["containerPort"], 3000)
        mounts = {m["name"]: m for m in container(app)["volumeMounts"]}
        self.assertEqual(mounts["files"]["mountPath"], "/usr/src/redmine/files")
        self.assertEqual(mounts["rack-config"]["mountPath"], "/usr/src/redmine/config.ru")
        volumes = {v["name"]: v for v in app["spec"]["template"]["spec"]["volumes"]}
        self.assertEqual(volumes["files"]["persistentVolumeClaim"]["claimName"], "media-data")
        self.assertEqual(mounts["files"]["subPath"], "development/redmine/redmine/files")
        self.assertEqual(volumes["rack-config"]["configMap"]["name"], "redmine-rack")
        self.assertIn("map '/redmine' do", objects(resources, "ConfigMap")["redmine-rack"]["data"]["config.ru"])
        self.assertEqual(env(app)["RAILS_RELATIVE_URL_ROOT"]["value"], "/redmine")
        self.assertEqual(container(app)["readinessProbe"]["httpGet"]["path"], "/redmine/login")
        self.assertEqual(services["redmine"]["metadata"]["annotations"]["kubarr.io/base-path"], "/redmine")
        self.assertEqual(objects(resources, "PersistentVolumeClaim")["media-data"]["spec"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(objects(resources, "PersistentVolumeClaim")["media-data"]["spec"]["storageClassName"], "")
        self.assertEqual(objects(resources, "PersistentVolumeClaim")["media-data"]["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        self.assertEqual(objects(resources, "PersistentVolume")["kubarr-media-redmine-media-data"]["spec"]["nfs"]["path"], "/")
        self.assertNotIn("volumeClaimTemplates", db["spec"])
        self.assertEqual(db["spec"]["template"]["spec"]["volumes"][1]["persistentVolumeClaim"]["claimName"], "media-data")
        self.assertEqual(container(db)["volumeMounts"][0]["subPath"], "development/redmine/redmine/postgres")
        self.assertEqual(db["spec"]["template"]["spec"]["initContainers"][0]["securityContext"]["capabilities"]["add"], ["CHOWN"])
        self.assertEqual(env(db)["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         env(app)["REDMINE_DB_PASSWORD"]["valueFrom"]["secretKeyRef"])
        self.assertEqual(env(app)["SECRET_KEY_BASE"]["valueFrom"]["secretKeyRef"],
                         {"name": "redmine-credentials", "key": "secret-key-base"})
        secret = objects(resources, "Secret")["redmine-credentials"]
        self.assertEqual(secret["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        self.assertEqual(set(secret["data"]), {"database-password", "secret-key-base"})
        self.assertNotEqual(secret["data"]["database-password"], secret["data"]["secret-key-base"])
        self.assertEqual(container(db)["image"], "docker.io/library/postgres:16.15-bookworm")
        for workload in (app, db):
            self.assertTrue(container(workload)["securityContext"]["runAsNonRoot"])
            self.assertFalse(container(workload)["securityContext"]["allowPrivilegeEscalation"])
            self.assertFalse(workload["spec"]["template"]["spec"]["automountServiceAccountToken"])
            self.assertIn("startupProbe", container(workload))
            self.assertIn("readinessProbe", container(workload))
        policies = objects(resources, "NetworkPolicy")
        self.assertEqual(set(policies), {"redmine", "redmine-postgres", "redmine-database-egress"})
        self.assertEqual(policies["redmine"]["spec"]["ingress"][0]["ports"][0]["port"], 3000)
        self.assertEqual(policies["redmine"]["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "openresty"})
        self.assertEqual(policies["redmine-postgres"]["spec"]["egress"], [])
        self.assertEqual(policies["redmine-postgres"]["spec"]["ingress"][0]["ports"][0]["port"], 5432)
        self.assertEqual(policies["redmine-database-egress"]["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"],
                         services["redmine-postgres"]["spec"]["selector"])

    def test_external_database_and_custom_claims(self):
        result, resources = render({
            "database": {"bundled": {"enabled": False}, "host": "pg.example", "port": 6432,
                         "username": "issues", "name": "tracker"},
            "secrets": {"existingSecret": "my-secret", "databasePasswordKey": "db-key", "sessionKey": "session-key"},
            "storage": {"attachments": {"existingClaim": "uploaded-files"}},
            "networkPolicy": {"egressTo": ["database-ns"]},
            "redmine": {"service": {"port": 8080, "annotations": {"example.com/a": "b"}}},
        }, release="issues")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "StatefulSet"))
        self.assertFalse(objects(resources, "PersistentVolumeClaim"))
        self.assertFalse(objects(resources, "Secret"))
        app = objects(resources, "Deployment")["issues-redmine"]
        self.assertEqual(env(app)["REDMINE_DB_POSTGRES"]["value"], "pg.example")
        self.assertEqual({item["name"]: item["value"] for item in app["spec"]["template"]["spec"]["initContainers"][-1]["env"]}["PGHOST"], "pg.example")
        self.assertEqual(env(app)["REDMINE_DB_PORT"]["value"], "6432")
        self.assertEqual(env(app)["REDMINE_DB_USERNAME"]["value"], "issues")
        self.assertEqual(env(app)["REDMINE_DB_DATABASE"]["value"], "tracker")
        self.assertEqual(env(app)["REDMINE_DB_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "my-secret", "key": "db-key"})
        self.assertEqual(env(app)["SECRET_KEY_BASE"]["valueFrom"]["secretKeyRef"],
                         {"name": "my-secret", "key": "session-key"})
        self.assertEqual(next(v for v in app["spec"]["template"]["spec"]["volumes"] if v["name"] == "files")["persistentVolumeClaim"]["claimName"], "uploaded-files")
        service = objects(resources, "Service")["issues-redmine"]
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)
        self.assertEqual(service["metadata"]["annotations"],
                         {"kubarr.io/base-path": "/redmine", "example.com/a": "b"})
        self.assertEqual(set(objects(resources, "NetworkPolicy")), {"issues"})

    def test_existing_local_db_claim_and_storage_classes(self):
        result, resources = render({"database": {"bundled": {"persistence": {"existingClaim": "local-pg"}}},
                                    "storage": {"attachments": {"storageClassName": "files-rwx"}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        db = objects(resources, "StatefulSet")["redmine-postgres"]
        self.assertNotIn("volumeClaimTemplates", db["spec"])
        self.assertEqual(db["spec"]["template"]["spec"]["volumes"][1]["persistentVolumeClaim"]["claimName"], "local-pg")
        self.assertEqual(objects(resources, "PersistentVolumeClaim")["redmine-files"]["spec"]["storageClassName"], "files-rwx")

    def test_invalid_configuration_fails(self):
        cases = [
            ({"database": {"bundled": {"enabled": False}, "host": "pg.example"}}, "secrets.existingSecret"),
            ({"secrets": {"sessionKey": ""}}, "secrets.sessionKey"),
            ({"database": {"bundled": {"enabled": False}}, "secrets": {"existingSecret": "external"}}, "database.host"),
            ({"database": {"bundled": {"persistence": {"subPath": "development/redmine/redmine/files"}}}}, "different NFS subPaths"),
            ({"database": {"bundled": {"persistence": {"subPath": "../secret"}}}}, "safe relative"),
            ({"secrets": {"sessionKey": "database-password"}}, "must differ"),
            ({"database": {"bundled": {"persistence": {"existingClaim": "redmine-files"}}}}, "must not share"),
            ({"redmine": {"service": {"type": "NodePort"}}}, "ClusterIP"),
            ({"redmine": {"basePath": ""}}, "redmine.basePath"),
        ]
        for override, message in cases:
            with self.subTest(message=message):
                result, _ = render(override)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_explicit_credentials_and_storage(self):
        result, resources = render({"secrets": {"existingSecret": "external-credentials"},
                                    "database": {"bundled": {"persistence": {"storageClassName": "fast-local"}}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "Secret"))
        self.assertEqual(env(objects(resources, "Deployment")["redmine"])["SECRET_KEY_BASE"]["valueFrom"]["secretKeyRef"]["name"], "external-credentials")
        self.assertEqual(objects(resources, "StatefulSet")["redmine-postgres"]["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"], "fast-local")

    def test_upgrade_refuses_missing_generated_credentials(self):
        result, _ = render(upgrade=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restore it from backup", result.stderr)
        result, resources = render({"secrets": {"existingSecret": "supplied"}}, upgrade=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "Secret"))

    def test_managed_export_defaults_to_sync(self):
        values = yaml.safe_load((ROOT / "system/managed-nfs/values.yaml").read_text())
        self.assertTrue(values["nfs"]["sync"])
        result = subprocess.run([os.environ.get("HELM", "helm"), "template", "managed-nfs",
                                 str(chart_input(ROOT / "system/managed-nfs"))],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        deployment = objects([r for r in yaml.safe_load_all(result.stdout) if r], "Deployment")["kubarr-managed-nfs"]
        self.assertIn({"name": "SYNC", "value": "true"}, deployment["spec"]["template"]["spec"]["containers"][0]["env"])
        self.assertEqual(deployment["metadata"]["labels"]["helm.sh/chart"], "managed-nfs-12.0.0_2")

    def test_external_root_squashed_export_uses_preprovisioned_paths(self):
        result, resources = render({"storage": {"media": {"nfs": {"server": "nas.example", "path": "/exports"}}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(objects(resources, "PersistentVolume")["kubarr-media-redmine-media-data"]["spec"]["nfs"],
                         {"server": "nas.example", "path": "/exports"})
        self.assertFalse(objects(resources, "StatefulSet")["redmine-postgres"]["spec"]["template"]["spec"].get("initContainers"))
        self.assertEqual(len(objects(resources, "Deployment")["redmine"]["spec"]["template"]["spec"]["initContainers"]), 1)

    def test_release_scoped_subpaths_are_distinct(self):
        result, resources = render(release="issues")
        self.assertEqual(result.returncode, 0, result.stderr)
        app = objects(resources, "Deployment")["issues-redmine"]
        db = objects(resources, "StatefulSet")["issues-redmine-postgres"]
        self.assertEqual(container(app)["volumeMounts"][-1]["subPath"], "development/redmine/issues-redmine/files")
        self.assertEqual(container(db)["volumeMounts"][0]["subPath"], "development/redmine/issues-redmine/postgres")


if __name__ == "__main__":
    unittest.main()
