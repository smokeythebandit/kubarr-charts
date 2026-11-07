"""The platform database runs as a CloudNativePG Cluster with nightly dumps.

The chart no longer owns a StatefulSet, a Service or a Secret: the operator
creates the instances and the `kubarr-db-rw` Service, and the Kubarr CLI owns
`kubarr-db-app`. What the chart owns is the Cluster's shape -- instance count
tied to the node count, one instance per node, data on the cluster's default
StorageClass -- and a nightly `pg_dump` to the shared export, so the data that
left NFS still has a copy there.
"""

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "system/postgresql"
OPERATOR = ROOT / "system/cloudnative-pg"


def render(chart=CHART, overrides=None, secret="kubarr-db-app"):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        values.write_text(yaml.safe_dump(overrides or {}))
        command = [os.environ.get("HELM", "helm"), "template", chart.name,
                   str(chart_input(chart)), "-f", str(values)]
        if secret is not None:
            command += ["--set", f"database.existingSecret={secret}"]
        result = subprocess.run(command, capture_output=True, text=True)
    docs = ([doc for doc in yaml.safe_load_all(result.stdout) if doc]
            if result.returncode == 0 else [])
    return result, docs


def only(docs, kind):
    found = [doc for doc in docs if doc["kind"] == kind]
    assert len(found) == 1, (kind, [d["metadata"]["name"] for d in found])
    return found[0]


def nodes(count):
    return {"kubarr": {"ha": {"nodeCount": count}}}


class ClusterShapeTests(unittest.TestCase):
    def cluster(self, overrides=None):
        result, docs = render(overrides=overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        return only(docs, "Cluster")

    def test_single_node_runs_one_instance(self):
        cluster = self.cluster()
        self.assertEqual(cluster["apiVersion"], "postgresql.cnpg.io/v1")
        self.assertEqual(cluster["metadata"]["name"], "kubarr-db")
        self.assertEqual(cluster["metadata"]["namespace"], "kubarr-database")
        self.assertEqual(cluster["spec"]["instances"], 1)
        self.assertEqual(self.cluster(nodes(1))["spec"]["instances"], 1)
        self.assertEqual(self.cluster(nodes(0))["spec"]["instances"], 1)

    def test_instances_follow_node_count_up_to_three(self):
        for count, expected in ((2, 2), (3, 3), (5, 3)):
            with self.subTest(nodes=count):
                self.assertEqual(self.cluster(nodes(count))["spec"]["instances"], expected)

    def test_instances_never_share_a_node(self):
        affinity = self.cluster(nodes(3))["spec"]["affinity"]
        self.assertEqual(affinity["podAntiAffinityType"], "required")
        self.assertEqual(affinity["topologyKey"], "kubernetes.io/hostname")

    def test_disruption_budget_only_with_a_replica_to_fail_over_to(self):
        # A PDB over a lone primary blocks every node drain on a single node.
        self.assertIs(self.cluster()["spec"]["enablePDB"], False)
        self.assertIs(self.cluster(nodes(1))["spec"]["enablePDB"], False)
        self.assertIs(self.cluster(nodes(2))["spec"]["enablePDB"], True)
        self.assertIs(self.cluster(nodes(3))["spec"]["enablePDB"], True)

    def test_bootstrap_uses_the_cli_secret(self):
        initdb = self.cluster()["spec"]["bootstrap"]["initdb"]
        self.assertEqual(initdb["database"], "kubarr")
        self.assertEqual(initdb["owner"], "kubarr")
        self.assertEqual(initdb["secret"], {"name": "kubarr-db-app"})

    def test_operand_is_postgres_16_matching_the_backup_client(self):
        result, docs = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        image = only(docs, "Cluster")["spec"]["imageName"]
        self.assertTrue(image.startswith("ghcr.io/cloudnative-pg/postgresql:16."), image)
        dump = only(docs, "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        client = dump["containers"][0]["image"]
        self.assertTrue(client.split(":", 1)[1].startswith("16."), client)

    def test_storage_uses_the_default_class_unless_one_is_named(self):
        storage = self.cluster()["spec"]["storage"]
        self.assertEqual(storage["size"], "5Gi")
        self.assertNotIn("storageClass", storage)
        storage = self.cluster({"persistence": {"size": "20Gi",
                                                "storageClassName": "fast-local"}})["spec"]["storage"]
        self.assertEqual(storage, {"size": "20Gi", "storageClass": "fast-local"})

    def test_an_empty_secret_name_refuses_to_render(self):
        result, _ = render(secret="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existingSecret", result.stderr)

    def test_the_operator_owns_every_workload_and_service(self):
        result, docs = render(overrides=nodes(3))
        self.assertEqual(result.returncode, 0, result.stderr)
        kinds = {doc["kind"] for doc in docs}
        for kind in ("StatefulSet", "Deployment", "Service", "Secret",
                     "VerticalPodAutoscaler"):
            self.assertNotIn(kind, kinds)


class BackupTests(unittest.TestCase):
    def cronjob(self, overrides=None):
        result, docs = render(overrides=overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        return only(docs, "CronJob"), docs

    def test_nightly_dump_on_schedule_without_overlap(self):
        cronjob, _ = self.cronjob()
        self.assertEqual(cronjob["metadata"]["namespace"], "kubarr-database")
        self.assertEqual(cronjob["spec"]["schedule"], "17 3 * * *")
        self.assertEqual(cronjob["spec"]["concurrencyPolicy"], "Forbid")

    def test_dump_lands_in_the_shared_export(self):
        cronjob, docs = self.cronjob()
        pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        container, = pod["containers"]
        mounts = [m for m in container["volumeMounts"] if m["name"] == "media"]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0]["subPath"], "system/postgresql-backups")
        self.assertFalse(mounts[0].get("readOnly", False))
        env = {e["name"]: e for e in container["env"]}
        self.assertEqual(env["BACKUP_DIR"]["value"], mounts[0]["mountPath"])
        self.assertEqual(env["DATABASE_URI"]["valueFrom"]["secretKeyRef"],
                         {"name": "kubarr-db-app", "key": "uri"})
        claim = next(v for v in pod["volumes"] if v["name"] == "media")
        self.assertEqual(claim["persistentVolumeClaim"]["claimName"], "media-data")
        self.assertIn("media-data", {d["metadata"]["name"] for d in docs
                                     if d["kind"] == "PersistentVolumeClaim"})
        prepare, = pod["initContainers"]
        self.assertEqual(prepare["args"], ["/nfs/system/postgresql-backups"])
        # The media identity, which `kubarr db restore` also reads as.
        self.assertEqual(pod["securityContext"]["runAsUser"], 1000)
        self.assertEqual(prepare["securityContext"]["runAsUser"], 1000)

    def test_retention_is_configurable(self):
        cronjob, _ = self.cronjob({"backup": {"retain": 3, "schedule": "0 1 * * *"}})
        self.assertEqual(cronjob["spec"]["schedule"], "0 1 * * *")
        container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual({e["name"]: e.get("value") for e in container["env"]}["RETAIN"], "3")

    def test_retention_below_one_refuses_to_render(self):
        for retain in (0, -1):
            with self.subTest(retain=retain):
                result, _ = render(overrides={"backup": {"retain": retain}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("backup.retain", result.stderr)

    def test_disabled_backup_renders_no_job_and_no_claim(self):
        result, docs = render(overrides={"backup": {"enabled": False}})
        self.assertEqual(result.returncode, 0, result.stderr)
        kinds = {doc["kind"] for doc in docs}
        self.assertNotIn("CronJob", kinds)
        self.assertNotIn("PersistentVolumeClaim", kinds)

    def test_backup_refuses_a_read_only_destination(self):
        mounts = [{"name": "backups", "source": "system/postgresql-backups",
                   "target": "/backups", "mode": "read"}]
        result, _ = render(overrides={"storage": {"mounts": mounts}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires", result.stderr)

    def test_script_writes_atomically_and_keeps_the_newest_seven(self):
        cronjob, _ = self.cronjob()
        container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e.get("value") for e in container["env"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backups, bin_dir = root / "backups", root / "bin"
            backups.mkdir()
            bin_dir.mkdir()
            # Nine older dumps, a stray file the job does not own, and a
            # partial left by an earlier interrupted run.
            old = [f"kubarr-2026010{day}T031700Z.dump" for day in range(1, 10)]
            stale = ".kubarr-20260101T031700Z.dump.partial"
            for name in old + ["notes.txt", stale]:
                (backups / name).write_text("old")
            fake = bin_dir / "pg_dump"
            # Records its arguments and writes to the --file it was handed,
            # which must not yet be the final name.
            fake.write_text('#!/bin/sh\n'
                            'for a do case "$a" in --file=*) f="${a#--file=}";; esac; done\n'
                            'case "$f" in */kubarr-*.dump) exit 9;; esac\n'
                            'printf "%s\\n" "$@" > "$BACKUP_DIR/../args"\n'
                            'echo dump > "$f"\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            run_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                       "BACKUP_DIR": str(backups), "RETAIN": env["RETAIN"],
                       "DATABASE_URI": "postgresql://kubarr:secret@kubarr-db-rw:5432/kubarr"}
            result = subprocess.run([*container["command"], *container.get("args", [])],
                                    env=run_env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = (root / "args").read_text().split()
            self.assertIn("-Fc", args)
            self.assertIn("--dbname=postgresql://kubarr:secret@kubarr-db-rw:5432/kubarr", args)
            dumps = sorted(p.name for p in backups.glob("kubarr-*.dump"))
            self.assertEqual(len(dumps), 7)
            new = [d for d in dumps if d not in old]
            self.assertEqual(len(new), 1)
            self.assertRegex(new[0], r"^kubarr-\d{8}T\d{6}Z\.dump$")
            self.assertEqual((backups / new[0]).read_text(), "dump\n")
            self.assertEqual(dumps[:6], old[-6:])
            self.assertTrue((backups / "notes.txt").exists())
            self.assertFalse((backups / stale).exists())
            self.assertEqual([p.name for p in backups.iterdir()
                              if not p.name.startswith("kubarr-") and p.name != "notes.txt"], [])

    def test_failed_dump_leaves_previous_dumps_and_no_partial(self):
        cronjob, _ = self.cronjob()
        container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backups, bin_dir = root / "backups", root / "bin"
            backups.mkdir()
            bin_dir.mkdir()
            old = [f"kubarr-2026010{day}T031700Z.dump" for day in range(1, 10)]
            for name in old:
                (backups / name).write_text("old")
            fake = bin_dir / "pg_dump"
            fake.write_text('#!/bin/sh\n'
                            'for a do case "$a" in --file=*) echo half > "${a#--file=}";; esac; done\n'
                            'exit 1\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            run_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                       "BACKUP_DIR": str(backups), "RETAIN": "7",
                       "DATABASE_URI": "postgresql://x"}
            result = subprocess.run([*container["command"], *container.get("args", [])],
                                    env=run_env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sorted(p.name for p in backups.iterdir()), old)


class NetworkPolicyTests(unittest.TestCase):
    def policy(self):
        result, docs = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        return only(docs, "NetworkPolicy")

    def test_selects_the_instances(self):
        spec = self.policy()["spec"]
        self.assertEqual(spec["podSelector"], {"matchLabels": {"cnpg.io/cluster": "kubarr-db"}})
        self.assertIn("Ingress", spec["policyTypes"])

    def test_admits_clients_backup_peers_and_operator(self):
        rules = self.policy()["spec"]["ingress"]

        def ports(rule):
            return sorted(p["port"] for p in rule["ports"])

        def admitted(selector):
            return [ports(rule) for rule in rules if selector in rule["from"]]

        for namespace in ("kubarr-backend", "kubarr-worker"):
            self.assertEqual(admitted({"namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": namespace}}}), [[5432]])
        # Selected on the component label alone: the CLI's restore pod
        # carries it under a different app name.
        self.assertEqual(admitted({"podSelector": {"matchLabels": {
            "app.kubernetes.io/component": "database-backup"}}}), [[5432]])
        self.assertEqual(admitted({"podSelector": {"matchLabels": {
            "cnpg.io/cluster": "kubarr-db"}}}), [[5432, 8000]])
        self.assertEqual(admitted({"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": "cnpg-system"}}}), [[8000]])

    def test_backup_pods_carry_the_admitted_labels(self):
        result, docs = render()
        labels = only(docs, "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
        self.assertEqual(labels["app.kubernetes.io/component"], "database-backup")


class OperatorChartTests(unittest.TestCase):
    def test_operator_is_a_hidden_system_component(self):
        metadata = yaml.safe_load((OPERATOR / "Chart.yaml").read_text())
        annotations = metadata["annotations"]
        self.assertEqual(annotations["kubarr.io/system"], "true")
        self.assertEqual(annotations["kubarr.io/hidden"], "true")
        self.assertEqual(annotations["kubarr.io/browseable"], "false")
        self.assertEqual(metadata["version"], "1.30.1+3")
        dependency = next(d for d in metadata["dependencies"] if d["name"] == "cloudnative-pg")
        self.assertEqual(dependency["repository"], "https://cloudnative-pg.github.io/charts")
        self.assertRegex(dependency["version"], r"^\d+\.\d+\.\d+$")

    def test_operator_namespace_must_agree_with_the_dependency(self):
        result, _ = render(OPERATOR, {"namespace": {"name": "elsewhere"}}, secret=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("namespaceOverride", result.stderr)

    def test_operator_renders_its_crds_and_a_bounded_controller(self):
        result, docs = render(OPERATOR, secret=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        namespace = only(docs, "Namespace")
        self.assertEqual(namespace["metadata"]["name"], "cnpg-system")
        self.assertEqual(only(docs, "Deployment")["metadata"]["namespace"], "cnpg-system")
        crds = {d["metadata"]["name"] for d in docs if d["kind"] == "CustomResourceDefinition"}
        self.assertIn("clusters.postgresql.cnpg.io", crds)
        deployment = only(docs, "Deployment")
        self.assertEqual(deployment["metadata"]["name"], "cloudnative-pg")
        container, = deployment["spec"]["template"]["spec"]["containers"]
        self.assertTrue(container["resources"]["requests"])
        self.assertTrue(container["resources"]["limits"])


if __name__ == "__main__":
    unittest.main()
