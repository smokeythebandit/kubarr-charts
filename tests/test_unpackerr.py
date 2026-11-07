"""Unpackerr extracts archived downloads in place for the managers to import.

It runs in folder mode: every writable mount under /downloads becomes a polled
watched folder (NFS delivers no file events from other nodes), and extracted
files go next to the archive, where Sonarr, Radarr and Lidarr look.
"""

import os
import subprocess
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


CHART = "download-client/unpackerr"


def render(*sets, check=True):
    command = [os.environ.get("HELM", "helm"), "template", "unpackerr",
               str(chart_input(ROOT / CHART))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result if not check else [d for d in yaml.safe_load_all(result.stdout) if d]


def kind(resources, name):
    return next(d for d in resources if d["kind"] == name)


class UnpackerrTests(unittest.TestCase):
    def test_every_download_mount_is_a_polled_folder_extracted_in_place(self):
        env = kind(render(), "ConfigMap")["data"]
        for client in ("qbittorrent", "transmission", "deluge", "rutorrent", "sabnzbd"):
            with self.subTest(client=client):
                self.assertEqual(env[f"UN_FOLDER_{client}_PATH"], f"/downloads/{client}")
                self.assertEqual(env[f"UN_FOLDER_{client}_INTERVAL"], "1m")
                self.assertEqual(env[f"UN_FOLDER_{client}_MOVE_BACK"], "true")
                # The archive keeps seeding; nothing is deleted by default.
                self.assertEqual(env[f"UN_FOLDER_{client}_DELETE_ORIGINAL"], "false")
                self.assertEqual(env[f"UN_FOLDER_{client}_DELETE_FILES"], "false")

    def test_deleting_the_extracted_copy_is_opt_in(self):
        env = kind(render("unpackerr.deleteExtractedAfter=2h"), "ConfigMap")["data"]
        self.assertEqual(env["UN_FOLDER_qbittorrent_DELETE_AFTER"], "2h")
        self.assertEqual(env["UN_FOLDER_qbittorrent_DELETE_FILES"], "true")
        self.assertEqual(env["UN_FOLDER_qbittorrent_DELETE_ORIGINAL"], "false")

    def test_only_writable_download_mounts_are_watched(self):
        env = kind(render(
            "storage.mounts[0].name=usenet-pull", "storage.mounts[0].source=downloads/sab",
            "storage.mounts[0].target=/downloads/sab", "storage.mounts[0].mode=read-write",
            "storage.mounts[1].name=library", "storage.mounts[1].source=media/shows",
            "storage.mounts[1].target=/media/shows", "storage.mounts[1].mode=read-write",
            "storage.mounts[2].name=ro", "storage.mounts[2].source=downloads/ro",
            "storage.mounts[2].target=/downloads/ro", "storage.mounts[2].mode=read",
        ), "ConfigMap")["data"]
        folders = sorted(k for k in env if k.endswith("_PATH"))
        self.assertEqual(folders, ["UN_FOLDER_usenet_pull_PATH"])

    def test_invalid_settings_are_refused(self):
        for value, message in (
            ("unpackerr.replicaCount=2", "replicaCount must be 1"),
            ("unpackerr.pollInterval=soon", "Go duration"),
            ("unpackerr.env.UN_FOLDER_x_PATH=/x", "derived from storage.mounts"),
            ("storage.mounts[0].target=/media/x", "at least one writable"),
        ):
            with self.subTest(value=value):
                result = render(value, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_pod_is_unprivileged_and_restarts_on_config_change(self):
        resources = render()
        deployment = kind(resources, "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        self.assertIs(pod["automountServiceAccountToken"], False)
        self.assertEqual(pod["securityContext"]["runAsUser"], 1000)
        app = next(c for c in pod["containers"] if c["name"] == "unpackerr")
        context = app["securityContext"]
        self.assertIs(context["readOnlyRootFilesystem"], True)
        self.assertIs(context["allowPrivilegeEscalation"], False)
        self.assertEqual(context["capabilities"]["drop"], ["ALL"])
        self.assertNotIn("ports", app)  # the web server stays off
        self.assertIn("checksum/config", deployment["spec"]["template"]["metadata"]["annotations"])
        mounts = {m["mountPath"] for m in app["volumeMounts"]}
        self.assertTrue({"/downloads/qbittorrent", "/tmp"} <= mounts)

    def test_network_policy_denies_everything_but_dns(self):
        policy = kind(render(), "NetworkPolicy")["spec"]
        self.assertEqual(policy["ingress"], [])
        self.assertEqual(len(policy["egress"]), 1)
        self.assertEqual({p["port"] for p in policy["egress"][0]["ports"]}, {53})

    def test_catalog_lists_it_without_an_open_tile(self):
        annotations = yaml.safe_load((ROOT / CHART / "Chart.yaml").read_text())["annotations"]
        self.assertEqual(annotations["kubarr.io/category"], "download-client")
        self.assertEqual(annotations["kubarr.io/browseable"], "false")
        self.assertEqual(annotations["kubarr.io/system"], "false")


if __name__ == "__main__":
    unittest.main()
