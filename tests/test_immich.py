"""Immich multi-workload, storage, secret and external-dependency render checks."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "media-server/immich"


def render(overrides=None, release="immich", upgrade=False):
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


def env_map(workload):
    return {e["name"]: e for e in workload["spec"]["template"]["spec"]["containers"][0]["env"]}


class ImmichTests(unittest.TestCase):
    def test_defaults_have_working_dependencies_and_distinct_storage(self):
        result, resources = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
        values = yaml.safe_load((CHART / "values.yaml").read_text())
        self.assertEqual(chart["annotations"]["kubarr.io/category"], "media-server")
        self.assertEqual(values["immich"]["image"]["tag"], values["machineLearning"]["image"]["tag"])
        deployments = objects(resources, "Deployment")
        services = objects(resources, "Service")
        db = objects(resources, "StatefulSet")["immich-postgres"]
        self.assertEqual(set(deployments), {"immich", "immich-ml", "immich-valkey"})
        for name in ("immich", "immich-ml"):
            self.assertFalse(deployments[name]["spec"]["template"]["spec"]["enableServiceLinks"],
                             "Kubernetes IMMICH_PORT=tcp://... service link breaks Immich environment parsing")
        self.assertEqual(set(services), {"immich", "immich-ml", "immich-postgres", "immich-valkey"})
        for name, component in (("immich", "server"), ("immich-ml", "ml"),
                                ("immich-valkey", "valkey"), ("immich-postgres", "postgres")):
            workload = db if name == "immich-postgres" else deployments[name]
            selector = services[name]["spec"]["selector"]
            self.assertEqual(selector["app.kubernetes.io/component"], component)
            self.assertTrue(all(workload["spec"]["template"]["metadata"]["labels"].get(k) == v
                                for k, v in selector.items()))
            container = workload["spec"]["template"]["spec"]["containers"][0]
            self.assertTrue(container["securityContext"]["runAsNonRoot"])
            self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
            self.assertFalse(any("hostPath" in v for v in workload["spec"]["template"]["spec"].get("volumes", [])))
        self.assertEqual(services["immich"]["spec"]["ports"][0]["port"], 2283)
        self.assertEqual(deployments["immich"]["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"], 2283)
        self.assertEqual(env_map(deployments["immich"])["DB_HOSTNAME"]["value"], "immich-postgres")
        self.assertEqual(env_map(deployments["immich"])["REDIS_HOSTNAME"]["value"], "immich-valkey")
        for workload in (deployments["immich"], db):
            key = "DB_PASSWORD" if workload is deployments["immich"] else "POSTGRES_PASSWORD"
            self.assertEqual(env_map(workload)[key]["valueFrom"]["secretKeyRef"],
                             {"name": "immich-db", "key": "DB_PASSWORD"})
        secret = objects(resources, "Secret")["immich-db"]
        self.assertEqual(secret["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        self.assertEqual(len(secret["data"]["DB_PASSWORD"]), 88)
        self.assertNotIn("storageClassName", db["spec"]["volumeClaimTemplates"][0]["spec"])
        self.assertEqual(db["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]["mountPath"],
                         "/var/lib/postgresql/data")
        self.assertEqual(objects(resources, "PersistentVolumeClaim")["media-data"]["spec"]["accessModes"],
                          ["ReadWriteMany"])
        self.assertNotIn("immich-ml", objects(resources, "PersistentVolumeClaim"))
        ml = deployments["immich-ml"]["spec"]["template"]["spec"]
        self.assertEqual(ml["volumes"][0]["persistentVolumeClaim"]["claimName"], "media-data")
        self.assertEqual(ml["containers"][0]["volumeMounts"][0]["subPath"], "media-server/immich/immich/models")
        self.assertEqual(ml["initContainers"][0]["securityContext"]["capabilities"]["add"], ["CHOWN"])
        self.assertEqual(deployments["immich"]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0],
                         {"name": "media", "mountPath": "/data", "subPath": "immich"})
        self.assertEqual(deployments["immich"]["spec"]["strategy"]["type"], "Recreate")
        policies = objects(resources, "NetworkPolicy")
        self.assertEqual(set(policies), {"immich", "immich-internal"})
        self.assertEqual(policies["immich"]["spec"]["ingress"][0]["ports"][0]["port"], 2283)
        self.assertEqual(policies["immich-internal"]["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"],
                         policies["immich"]["spec"]["podSelector"]["matchLabels"])
        self.assertEqual(policies["immich"]["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"],
                         {"kubernetes.io/metadata.name": "openresty"})

    def test_custom_release_external_services_and_claims(self):
        overrides = {
            "storage": {"media": {"create": False, "existingClaim": "photos", "subPath": "photos"}},
            "database": {"bundled": {"enabled": False}, "host": "pg.example", "port": 6432,
                         "existingSecret": "database-key", "passwordKey": "password"},
            "cache": {"bundled": {"enabled": False}, "host": "redis.example", "existingSecret": "redis-key"},
            "machineLearning": {"cache": {"existingClaim": "models"}},
            "networkPolicy": {"egressTo": ["external-services"]},
        }
        result, resources = render(overrides, "photos")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "StatefulSet"))
        self.assertEqual(set(objects(resources, "Deployment")), {"photos-immich", "photos-immich-ml"})
        self.assertFalse(objects(resources, "PersistentVolumeClaim"))
        self.assertFalse(objects(resources, "PersistentVolume"))
        self.assertFalse(objects(resources, "Secret"))
        server = objects(resources, "Deployment")["photos-immich"]
        env = env_map(server)
        self.assertEqual(env["DB_HOSTNAME"]["value"], "pg.example")
        self.assertEqual(env["DB_PORT"]["value"], "6432")
        self.assertEqual(env["REDIS_HOSTNAME"]["value"], "redis.example")
        self.assertEqual(env["REDIS_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"], "redis-key")
        self.assertEqual(env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "database-key", "key": "password"})
        self.assertEqual(objects(resources, "Service")["photos-immich"]["spec"]["selector"]["app.kubernetes.io/component"], "server")

    def test_ml_disabled_and_service_port_changed(self):
        result, resources = render({"machineLearning": {"enabled": False},
                                    "immich": {"service": {"port": 8080}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("immich-ml", objects(resources, "Deployment"))
        self.assertNotIn("immich-ml", objects(resources, "PersistentVolumeClaim"))
        self.assertNotIn("IMMICH_MACHINE_LEARNING_URL",
                         env_map(objects(resources, "Deployment")["immich"]))
        self.assertEqual(objects(resources, "Service")["immich"]["spec"]["ports"][0]["port"], 8080)
        self.assertEqual(objects(resources, "NetworkPolicy")["immich"]["spec"]["ingress"][0]["ports"][0]["port"], 2283)

    def test_long_fullname_keeps_all_component_names_valid(self):
        result, resources = render({"fullnameOverride": "photos-" * 11})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(len(r["metadata"]["name"]) <= 63 for r in resources))
        server = next(r for r in resources if r["kind"] == "Deployment" and r["spec"]["template"]["spec"]["containers"][0]["name"] == "immich")
        self.assertIn(env_map(server)["DB_HOSTNAME"]["value"], objects(resources, "Service"))

    def test_invalid_dependencies_fail_early(self):
        cases = [
            ({"database": {"bundled": {"enabled": False}, "host": "pg.example"}}, "database.existingSecret"),
            ({"database": {"bundled": {"enabled": False}}}, "database.existingSecret"),
            ({"cache": {"bundled": {"enabled": False}}}, "cache.host"),
            ({"database": {"bundled": {"persistence": {"existingClaim": "media-data"}}}}, "shared media claim"),
            ({"database": {"bundled": {"persistence": {"storageClassName": "nfs-client"}}}}, "must not be NFS"),
            ({"database": {"passwordKey": ""}}, "database.passwordKey"),
            ({"machineLearning": {"cache": {"subPath": "../other"}}}, "safe relative"),
            ({"cache": {"existingSecret": "redis-key"}}, "Bundled Valkey has no authentication"),
            ({"database": {"port": 6432}}, "Bundled PostgreSQL listens on port 5432"),
            ({"cache": {"port": 6380}}, "Bundled Valkey listens on port 6379"),
            ({"immich": {"replicaCount": 2}}, "immich.replicaCount"),
            ({"immich": {"service": {"type": "NodePort"}}}, "ClusterIP"),
            ({"storage": {"media": {"existingClaim": ""}}}, "storage.media.existingClaim"),
        ]
        for override, message in cases:
            with self.subTest(message=message):
                result, _ = render(override)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_explicit_secret_and_storage_override(self):
        result, resources = render({"database": {"existingSecret": "owned-db", "passwordKey": "pwd",
                                               "bundled": {"persistence": {"storageClassName": "fast-local"}}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "Secret"))
        self.assertEqual(env_map(objects(resources, "Deployment")["immich"])["DB_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "owned-db", "key": "pwd"})
        self.assertEqual(objects(resources, "StatefulSet")["immich-postgres"]["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"], "fast-local")

    def test_upgrade_refuses_missing_generated_password(self):
        result, _ = render(upgrade=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restore it from backup", result.stderr)
        result, resources = render({"database": {"existingSecret": "supplied"}}, upgrade=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(objects(resources, "Secret"))


if __name__ == "__main__":
    unittest.main()
