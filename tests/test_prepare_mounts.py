"""Every NFS subpath is prepared by the shared unprivileged helper: kubelet
creates missing subPath directories as root, which a root-squashed export
refuses, whether or not the mount is read-only."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input, credential_args


# uid that must own each prepared directory: the identity the application writes as.
UIDS = {
    "development/jenkins": 1000,
    "development/redmine": 999,
    "development/scm-manager": 1000,
    "download-client/deluge": 1000,
    "download-client/qbittorrent": 1000,
    "download-client/rutorrent": 1000,
    "download-client/sabnzbd": 1000,
    "download-client/transmission": 1000,
    "media-manager/bazarr": 1000,
    "media-manager/jellyseerr": 1000,
    "media-manager/lidarr": 1000,
    "media-manager/radarr": 1000,
    "media-manager/sonarr": 1000,
    "media-server/audiobookshelf": 1000,
    "media-server/calibre-web-automated": 1000,
    "media-server/frigate": 1000,
    "media-server/immich": 1000,
    "media-server/jellyfin": 1000,
    "media-server/komga": 1000,
    "media-server/navidrome": 1000,
    "media-server/plex": 1000,
    "media-server/tdarr": 1000,
    "monitoring/victorialogs": 1000,
    "monitoring/victoriametrics": 65534,
    "system/postgresql": 1000,  # the nightly dump CronJob
}
# Consumers with no writable subPath to prepare: no init container may appear.
WITHOUT_PREPARATION = ("indexer/jackett", "monitoring/headlamp", "system/kubarr-backend")


# The install gate's shared export: an external, root-squashed NFS server.
EXTERNAL_NFS = ["--set", "storage.media.nfs.server=192.0.2.10", "--set", "storage.media.nfs.path=/"]


def workloads(chart, extra=()):
    path = ROOT / chart
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", path.name, str(chart_input(path)),
         *credential_args(path), *extra],
        check=True, capture_output=True, text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout)
            if doc and doc.get("kind") in ("Deployment", "StatefulSet", "DaemonSet", "CronJob")]


def pod_spec(workload):
    if workload["kind"] == "CronJob":
        return workload["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return workload["spec"]["template"]["spec"]


def prepared_sources(pod):
    """Media subPaths the application containers mount, as (writable, read-only)."""
    writable, read_only = set(), set()
    for container in pod.get("containers", []) + pod.get("initContainers", []):
        if container["name"] == "prepare-media-directories":
            continue
        for mount in container.get("volumeMounts", []):
            if mount["name"] == "media" and "subPath" in mount:
                (read_only if mount.get("readOnly") else writable).add(mount["subPath"])
    return writable, read_only - writable


def split_args(args):
    """The helper lists writable directories, then --read-only, then the rest."""
    if "--read-only" in args:
        index = args.index("--read-only")
        return args[:index], args[index + 1:]
    return args, []


class PrepareMountsTests(unittest.TestCase):
    def test_every_subpath_is_prepared_exactly_once(self):
        for storage, extra in (("managed", ()), ("external", EXTERNAL_NFS)):
            for chart, uid in UIDS.items():
                for workload in workloads(chart, extra):
                    self.assert_prepared(storage, chart, uid, workload)

    def assert_prepared(self, storage, chart, uid, workload):
        pod = pod_spec(workload)
        writable, read_only = prepared_sources(pod)
        name = workload["metadata"]["name"]
        with self.subTest(storage=storage, chart=chart, workload=name):
            prepare = [c for c in pod.get("initContainers", [])
                       if c["name"] == "prepare-media-directories"]
            if not writable and not read_only:
                self.assertEqual(prepare, [])
                return
            self.assertEqual(len(prepare), 1)
            args = prepare[0]["args"]
            rw_args, ro_args = split_args(args)
            self.assertEqual(sorted(rw_args), sorted(f"/nfs/{s}" for s in writable))
            self.assertEqual(sorted(ro_args), sorted(f"/nfs/{s}" for s in read_only))
            self.assertEqual(len(args), len(set(args)))
            self.assertEqual(prepare[0]["volumeMounts"],
                             [{"name": "media", "mountPath": "/nfs"}])
            context = prepare[0]["securityContext"]
            self.assertEqual(context["runAsUser"], uid)
            self.assertEqual(context["runAsGroup"], 1000)
            self.assertTrue(context["runAsNonRoot"])
            self.assertFalse(context["allowPrivilegeEscalation"])
            self.assertEqual(context["capabilities"], {"drop": ["ALL"]})

    def test_no_preparation_step_relies_on_root_or_chown(self):
        """Only steps that touch the shared export are constrained. A container
        fixing up its own in-Pod directories is unaffected by root squashing."""
        for chart in list(UIDS) + list(WITHOUT_PREPARATION):
            for workload in workloads(chart):
                pod = pod_spec(workload)
                for container in pod.get("initContainers", []):
                    if not any(mount["name"] == "media"
                               for mount in container.get("volumeMounts", [])):
                        continue
                    with self.subTest(chart=chart, container=container["name"]):
                        context = container.get("securityContext", {})
                        self.assertNotEqual(context.get("runAsUser"), 0)
                        self.assertNotIn("CHOWN", context.get("capabilities", {}).get("add", []))
                        self.assertNotIn("chown", " ".join(container.get("command", [])))

    def test_consumers_without_writable_subpaths_prepare_nothing(self):
        for chart in WITHOUT_PREPARATION:
            for workload in workloads(chart):
                pod = pod_spec(workload)
                with self.subTest(chart=chart):
                    self.assertEqual(
                        [c for c in pod.get("initContainers", [])
                         if c["name"] == "prepare-media-directories"], [])

    def test_rendered_command_is_idempotent_and_group_writable(self):
        pod = pod_spec(workloads("system/postgresql")[0])
        prepare, = [c for c in pod["initContainers"]
                    if c["name"] == "prepare-media-directories"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "system" / "postgresql-backups"
            for _ in range(2):
                subprocess.run([*prepare["command"], str(target)],
                               check=True, capture_output=True, text=True)
            self.assertTrue(target.is_dir())
            self.assertEqual(target.stat().st_mode & 0o777, 0o775)


LIBRARY_MOUNTS = {
    "media-manager/sonarr": ("media/shows", "/media/shows", "read-write"),
    "media-manager/radarr": ("media/movies", "/media/movies", "read-write"),
    "media-server/jellyfin": ("media", "/media", "read"),
    "media-server/plex": ("media", "/media", "read"),
}


class LibraryPathTests(unittest.TestCase):
    def test_library_mounts_use_identical_export_and_container_paths(self):
        for chart, (source, target, mode) in LIBRARY_MOUNTS.items():
            with self.subTest(chart=chart):
                mounts = yaml.safe_load(
                    (ROOT / chart / "values.yaml").read_text())["storage"]["mounts"]
                library = next(m for m in mounts if m["name"] == "library")
                self.assertEqual(library["source"], source)
                self.assertEqual(library["target"], target)
                self.assertEqual(library["mode"], mode)
                self.assertEqual(library["target"], "/" + library["source"])


if __name__ == "__main__":
    unittest.main()
