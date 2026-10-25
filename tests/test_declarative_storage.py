"""Rendered declarative storage contract across shared NFS consumers."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


PRIVATE_CONFIG = (
    "media-manager/radarr", "media-manager/sonarr", "media-manager/jellyseerr",
    "media-server/plex", "media-server/jellyfin", "indexer/jackett",
    "download-client/deluge", "download-client/qbittorrent",
    "download-client/transmission", "download-client/rutorrent", "download-client/sabnzbd",
)
NFS_CHARTS = PRIVATE_CONFIG + (
    "development/jenkins", "development/redmine", "development/scm-manager",
    "media-server/immich", "media-server/frigate", "monitoring/victoriametrics",
    "monitoring/victorialogs", "system/postgresql", "system/kubarr-backend", "system/kubarr-worker",
)
OTHER_CHARTS = (
    "monitoring/fluent-bit", "monitoring/grafana", "monitoring/headlamp",
    "system/managed-nfs", "system/cert-manager", "system/cloudflared",
    "system/kubarr-dns-webhook", "system/kubarr-frontend", "system/openresty",
    "system/kubarr-common",
)


def render(chart, overrides=None, upgrade=False):
    path = ROOT / chart
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        values.write_text(yaml.safe_dump(overrides or {}))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", path.name,
             str(chart_input(path)), "-f", str(values),
             *(["--is-upgrade"] if upgrade else [])],
            capture_output=True, text=True,
        )
    return result, ([r for r in yaml.safe_load_all(result.stdout) if r]
                    if result.returncode == 0 else [])


def pods(resources):
    for resource in resources:
        if resource["kind"] in ("Deployment", "StatefulSet", "DaemonSet"):
            yield resource["metadata"]["name"], resource["spec"]["template"]["spec"]


class DeclarativeStorageTests(unittest.TestCase):
    def test_private_config_is_mounted_at_the_application_config_directory(self):
        for chart in PRIVATE_CONFIG:
            with self.subTest(chart=chart):
                result, resources = render(chart)
                self.assertEqual(result.returncode, 0, result.stderr)
                name = Path(chart).name
                pod = next(pod for _, pod in pods(resources)
                           if any(container["name"] == name for container in pod["containers"]))
                container = next(container for container in pod["containers"] if container["name"] == name)
                mount = next(mount for mount in container["volumeMounts"] if mount["name"] == "config")
                self.assertEqual(mount["mountPath"], "/app/config" if name == "jellyseerr" else "/config")
                volume = next(volume for volume in pod["volumes"] if volume["name"] == "config")
                self.assertNotEqual(volume["persistentVolumeClaim"]["claimName"], "media-data")

    def test_persistent_nfs_state_cannot_silently_become_ephemeral(self):
        required = {
            "development/jenkins": "/var/jenkins_home",
            "development/scm-manager": "/var/lib/scm",
            "development/redmine": "/usr/src/redmine/files",
            "media-server/immich": "/data",
            "media-server/frigate": "/media/frigate",
            "monitoring/victoriametrics": "/victoria-metrics-data",
            "monitoring/victorialogs": "/data",
            "system/postgresql": "/var/lib/postgresql/data",
            **{f"download-client/{client}": "/downloads" for client in (
                "deluge", "qbittorrent", "rutorrent", "sabnzbd", "transmission")},
        }
        for chart, target in required.items():
            with self.subTest(chart=chart):
                defaults = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["storage"]["mounts"]
                for mounts in ([], [{**m, "mode": "read"} if m["target"] == target else m
                                    for m in defaults],
                               [{**m, "target": "/scratch"} if m["target"] == target else m
                                for m in defaults]):
                    result, _ = render(chart, {"storage": {"mounts": mounts}})
                    self.assertNotEqual(result.returncode, 0, (chart, mounts))
                    self.assertIn("requires", result.stderr)

    def test_prepare_home_uses_matching_target_not_first_list_entry(self):
        for chart, target in (("development/jenkins", "/var/jenkins_home"),
                              ("development/scm-manager", "/var/lib/scm"),
                              ("development/redmine", "/usr/src/redmine/files")):
            mounts = [dict(name="scratch", source="scratch", target="/scratch", mode="read-write"),
                      dict(name="home", source="separate-home", target=target, mode="read-write")]
            result, resources = render(chart, {"storage": {"mounts": mounts}})
            self.assertEqual(result.returncode, 0, result.stderr)
            prep = [c for _, pod in pods(resources) for c in pod.get("initContainers", [])
                    if c["name"] in ("prepare-home", "prepare-files")]
            self.assertEqual(len(prep), 1)
            self.assertIn("/nfs/separate-home", prep[0]["command"])

    def test_redmine_fixed_config_mount_cannot_be_hidden(self):
        for target in ("/usr/src/redmine", "/usr/src/redmine/config.ru",
                       "/usr/src/redmine/config.ru/child"):
            mounts = [dict(name="files", source="development/redmine/redmine/files",
                           target="/usr/src/redmine/files", mode="read-write"),
                      dict(name="hidden", source="unrelated", target=target, mode="read-write")]
            result, _ = render("development/redmine", {"storage": {"mounts": mounts}})
            self.assertNotEqual(result.returncode, 0, target)
            self.assertTrue("overlaps" in result.stderr or "absolute path" in result.stderr)

    def test_nfs_backend_fields_fail_before_manifest_emission(self):
        invalid = (("path", "relative"), ("path", True), ("path", "/data/../config"),
                   ("path", ""), ("server", True), ("server", ""), ("server", "/unsafe"),
                   ("size", "0Gi"), ("size", False))
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                result, _ = render("media-server/plex", {"storage": {"media": {"nfs": {field: value}}}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"storage.media.nfs.{field}", result.stderr)
        result, resources = render("media-server/plex", {"storage": {"media": {"nfs": {
            "server": "2001:db8::1", "path": "/my-export/photos", "size": "2Ti"}}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        pv = next(r for r in resources if r["kind"] == "PersistentVolume")
        self.assertEqual(pv["spec"]["nfs"], {"server": "2001:db8::1", "path": "/my-export/photos"})

    def test_exporters_mount_private_config_only(self):
        for chart in ("media-manager/radarr", "media-manager/sonarr"):
            result, resources = render(chart)
            self.assertEqual(result.returncode, 0, result.stderr)
            exporter = next(pod for _, pod in pods(resources)
                            if any(c["name"] == "exportarr" for c in pod["containers"]))
            self.assertEqual([v["name"] for v in exporter["volumes"]], ["config"])
            self.assertFalse(any(m["name"] == "media" for c in exporter["containers"]
                                 for m in c.get("volumeMounts", [])))

    def test_non_nfs_charts_do_not_gain_shared_export_volumes(self):
        self.assertEqual(len(NFS_CHARTS) + len(OTHER_CHARTS), 31)
        for chart in OTHER_CHARTS:
            with self.subTest(chart=chart):
                if chart == "system/kubarr-common":
                    self.assertEqual(yaml.safe_load((ROOT / chart / "Chart.yaml").read_text())["type"], "library")
                    continue
                result, resources = render(chart)
                self.assertEqual(result.returncode, 0, result.stderr)
                nfs_volumes = [r for r in resources
                               if r["kind"] == "PersistentVolume" and "nfs" in r["spec"]]
                if chart == "monitoring/fluent-bit":
                    # Keep the former shared storage without reattaching it to
                    # the collector's node-local tail-position database.
                    self.assertEqual(len(nfs_volumes), 1)
                    self.assertEqual(nfs_volumes[0]["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
                    retained = [r for r in resources if r["kind"] == "PersistentVolumeClaim"
                                and r["spec"].get("volumeName") == nfs_volumes[0]["metadata"]["name"]]
                    self.assertEqual(len(retained), 1)
                    self.assertEqual(retained[0]["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
                    for _, pod in pods(resources):
                        self.assertFalse(any(
                            "nfs" in volume or volume.get("persistentVolumeClaim", {}).get("claimName") == retained[0]["metadata"]["name"]
                            for volume in pod.get("volumes", [])
                        ))
                else:
                    self.assertEqual(nfs_volumes, [])

    def test_all_nfs_workloads_mount_only_their_declared_paths(self):
        for chart in NFS_CHARTS:
            with self.subTest(chart=chart):
                defaults = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["storage"]
                result, resources = render(chart)
                self.assertEqual(result.returncode, 0, result.stderr)
                main_name = {"system/kubarr-backend": "backend", "system/kubarr-worker": "worker"}.get(
                    chart, chart.split("/")[-1])
                expected = {main_name: defaults["mounts"]}
                if chart == "media-server/immich":
                    expected["machine-learning"] = defaults["workloads"]["machineLearning"]["mounts"]
                if chart == "development/redmine":
                    expected["postgres"] = defaults["workloads"]["postgres"]["mounts"]
                for _, pod in pods(resources):
                    volumes = {v["name"] for v in pod.get("volumes", [])}
                    if chart == "media-server/immich" and any(c["name"] == "postgres" for c in pod["containers"]):
                        volumes.add("data") # StatefulSet volumeClaimTemplates
                    for c in pod["containers"]:
                        mounts = c.get("volumeMounts", [])
                        self.assertTrue({m["name"] for m in mounts} <= volumes)
                        actual = [(m["mountPath"], m.get("subPath"), m.get("readOnly", False))
                                  for m in mounts if m["name"] == "media"]
                        declared = [(m["target"], None if m["source"] == "." else m["source"],
                                     m["mode"] == "read") for m in expected.get(c["name"], [])]
                        self.assertEqual(actual, declared, (chart, c["name"]))
                    for c in pod.get("initContainers", []):
                        exposed = [m for m in c.get("volumeMounts", []) if m["name"] == "media"]
                        if exposed:
                            self.assertTrue(c["name"].startswith("prepare-"), (chart, c["name"]))
                            self.assertEqual([m["mountPath"] for m in exposed], ["/nfs"])

    def test_every_nfs_default_is_a_concrete_list_and_renders_exact_mounts(self):
        for chart in NFS_CHARTS:
            with self.subTest(chart=chart):
                defaults = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["storage"]
                self.assertIsInstance(defaults["mounts"], list)
                result, resources = render(chart)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("media-data", {r["metadata"]["name"] for r in resources
                                             if r["kind"] == "PersistentVolumeClaim"})
                namespace = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["namespace"]["name"]
                self.assertIn(f"kubarr-media-{namespace}-media-data",
                              {r["metadata"]["name"] for r in resources if r["kind"] == "PersistentVolume"})
                for r in resources:
                    if r["kind"] in ("PersistentVolume", "PersistentVolumeClaim") and r["metadata"]["name"] in (
                            "media-data", f"kubarr-media-{namespace}-media-data"):
                        self.assertEqual(r["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
                if chart in PRIVATE_CONFIG:
                    config = f"{chart.split('/')[-1]}-config"
                    claims = {r["metadata"]["name"]: r for r in resources
                              if r["kind"] == "PersistentVolumeClaim"}
                    self.assertEqual(claims[config]["spec"]["accessModes"], ["ReadWriteOnce"])
                    self.assertEqual(claims[config]["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
                    self.assertEqual({m["subPath"] for _, pod in pods(resources)
                                      for c in pod["containers"] + [init for init in pod.get("initContainers", [])
                                                                   if not init["name"].startswith("prepare-")]
                                      for m in c.get("volumeMounts", []) if m["name"] == "media"},
                                     {m["source"] for m in defaults["mounts"]})

    def test_private_config_upgrade_requires_acknowledgement(self):
        for chart in PRIVATE_CONFIG:
            with self.subTest(chart=chart):
                result, _ = render(chart, upgrade=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("storage.config.migrationConfirmed", result.stderr)
                result, _ = render(chart, {"storage": {"config": {"migrationConfirmed": True}}}, upgrade=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_keys_fail_closed_including_reused_null(self):
        for chart in NFS_CHARTS:
            with self.subTest(chart=chart):
                for key, val in (("layout", "split"), ("layout", None), ("profile", "restricted"),
                                 ("mountEnabled", False), ("readOnly", True)):
                    result, _ = render(chart, {"storage": {key: val}})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"storage.{key} was removed", result.stderr)
                result, _ = render(chart, {"storage": {"media": {"configSubPath": None}}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("storage.media.configSubPath was removed", result.stderr)

    def test_empty_and_invalid_lists_are_not_implicit_roles(self):
        for chart in PRIVATE_CONFIG:
            with self.subTest(chart=chart):
                if chart.startswith("download-client/"):
                    result, _ = render(chart, {"storage": {"mounts": []}})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("/downloads", result.stderr)
                    continue
                result, resources = render(chart, {"storage": {"mounts": []}})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(m["name"] == "media" for _, pod in pods(resources)
                                     for c in pod.get("initContainers", []) + pod["containers"]
                                     for m in c.get("volumeMounts", [])))
                self.assertIn("media-data", {r["metadata"]["name"] for r in resources
                                             if r["kind"] == "PersistentVolumeClaim"})
                result, _ = render(chart, {"storage": {"mounts": None}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("storage.mounts must be a list", result.stderr)

    def test_mount_validation_and_download_setup_selection(self):
        chart = "media-manager/radarr"
        for entry in (dict(name="bad", source="../config", target="/data", mode="read"),
                      dict(name="bad", source="media", target="/config", mode="read"),
                      dict(name="bad", source="media", target="/data", mode="rw")):
            result, _ = render(chart, {"storage": {"mounts": [entry]}})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("storage.mounts[0]", result.stderr)
        for chart in ("media-manager/radarr", "media-manager/sonarr"):
            result, resources = render(chart, {"storage": {"mounts": [
                dict(name="qbittorrent", source="downloads/qbittorrent",
                     target="/downloads/qbittorrent", mode="read-write")]}})
            self.assertEqual(result.returncode, 0, result.stderr)
            script = next(r["data"]["setup-downloadclients.sh"] for r in resources
                          if r["kind"] == "ConfigMap" and r["metadata"]["name"] == f"{chart.split('/')[-1]}-setup")
            table = script.split('CLIENTS="', 1)[1].split('"', 1)[0]
            self.assertIn("qBittorrent|", table)
            self.assertNotIn("SABnzbd|", table)


if __name__ == "__main__":
    unittest.main()
