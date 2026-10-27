"""Every writable NFS subpath is prepared by the shared unprivileged helper."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


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
    "media-manager/jellyseerr": 1000,
    "media-manager/radarr": 1000,
    "media-manager/sonarr": 1000,
    "media-server/frigate": 1000,
    "media-server/immich": 1000,
    "media-server/jellyfin": 1000,
    "media-server/plex": 1000,
    "monitoring/victorialogs": 1000,
    "monitoring/victoriametrics": 65534,
    "system/postgresql": 999,
}
# Consumers with no writable subPath to prepare: no init container may appear.
WITHOUT_PREPARATION = ("indexer/jackett", "monitoring/headlamp", "system/kubarr-backend")


def workloads(chart):
    path = ROOT / chart
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", path.name, str(chart_input(path))],
        check=True, capture_output=True, text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout)
            if doc and doc.get("kind") in ("Deployment", "StatefulSet", "DaemonSet")]


def prepared_sources(pod):
    """Writable media subPaths the application containers actually mount."""
    sources = set()
    for container in pod.get("containers", []) + pod.get("initContainers", []):
        if container["name"] == "prepare-media-directories":
            continue
        for mount in container.get("volumeMounts", []):
            if mount["name"] == "media" and not mount.get("readOnly") and "subPath" in mount:
                sources.add(mount["subPath"])
    return sources


class PrepareMountsTests(unittest.TestCase):
    def test_every_writable_subpath_is_prepared_exactly_once(self):
        for chart, uid in UIDS.items():
            for workload in workloads(chart):
                pod = workload["spec"]["template"]["spec"]
                sources = prepared_sources(pod)
                name = workload["metadata"]["name"]
                with self.subTest(chart=chart, workload=name):
                    prepare = [c for c in pod.get("initContainers", [])
                               if c["name"] == "prepare-media-directories"]
                    if not sources:
                        self.assertEqual(prepare, [])
                        continue
                    self.assertEqual(len(prepare), 1)
                    args = prepare[0]["args"]
                    self.assertEqual(sorted(args), sorted(f"/nfs/{s}" for s in sources))
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
        for chart in list(UIDS) + list(WITHOUT_PREPARATION):
            for workload in workloads(chart):
                pod = workload["spec"]["template"]["spec"]
                for container in pod.get("initContainers", []):
                    with self.subTest(chart=chart, container=container["name"]):
                        context = container.get("securityContext", {})
                        self.assertNotEqual(context.get("runAsUser"), 0)
                        self.assertNotIn("CHOWN", context.get("capabilities", {}).get("add", []))
                        self.assertNotIn("chown", " ".join(container.get("command", [])))

    def test_consumers_without_writable_subpaths_prepare_nothing(self):
        for chart in WITHOUT_PREPARATION:
            for workload in workloads(chart):
                pod = workload["spec"]["template"]["spec"]
                with self.subTest(chart=chart):
                    self.assertEqual(
                        [c for c in pod.get("initContainers", [])
                         if c["name"] == "prepare-media-directories"], [])

    def test_rendered_command_is_idempotent_and_group_writable(self):
        pod = workloads("system/postgresql")[0]["spec"]["template"]["spec"]
        prepare, = [c for c in pod["initContainers"]
                    if c["name"] == "prepare-media-directories"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "system" / "postgresql"
            for _ in range(2):
                subprocess.run([*prepare["command"], str(target)],
                               check=True, capture_output=True, text=True)
            self.assertTrue(target.is_dir())
            self.assertEqual(target.stat().st_mode & 0o777, 0o775)


if __name__ == "__main__":
    unittest.main()
