"""Unpackerr extracts archived downloads in place for the managers to import.

By default it follows Sonarr, Radarr and Lidarr's queues with the URLs and keys
Kubarr writes into a Secret (starrLink). In folder mode every writable mount
under /downloads becomes a polled watched folder (NFS delivers no file events
from other nodes), and extracted files go next to the archive.
"""

import os
import subprocess
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


CHART = "download-client/unpackerr"
FOLDERS = "unpackerr.folderWatch=true"


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
        env = kind(render(FOLDERS), "ConfigMap")["data"]
        for client in ("qbittorrent", "transmission", "deluge", "rutorrent", "sabnzbd"):
            with self.subTest(client=client):
                self.assertEqual(env[f"UN_FOLDER_{client}_PATH"], f"/downloads/{client}")
                self.assertEqual(env[f"UN_FOLDER_{client}_INTERVAL"], "1m")
                self.assertEqual(env[f"UN_FOLDER_{client}_MOVE_BACK"], "true")
                # The archive keeps seeding; nothing is deleted by default.
                self.assertEqual(env[f"UN_FOLDER_{client}_DELETE_ORIGINAL"], "false")
                self.assertEqual(env[f"UN_FOLDER_{client}_DELETE_FILES"], "false")

    def test_deleting_the_extracted_copy_is_opt_in(self):
        env = kind(render(FOLDERS, "unpackerr.deleteExtractedAfter=2h"), "ConfigMap")["data"]
        self.assertEqual(env["UN_FOLDER_qbittorrent_DELETE_AFTER"], "2h")
        self.assertEqual(env["UN_FOLDER_qbittorrent_DELETE_FILES"], "true")
        self.assertEqual(env["UN_FOLDER_qbittorrent_DELETE_ORIGINAL"], "false")

    def test_only_writable_download_mounts_are_watched(self):
        env = kind(render(
            FOLDERS,
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
                result = render(FOLDERS, value, check=False)
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
        self.assertIn("checksum/config", deployment["spec"]["template"]["metadata"]["annotations"])
        mounts = {m["mountPath"] for m in app["volumeMounts"]}
        self.assertTrue({"/downloads/qbittorrent", "/tmp"} <= mounts)

    def test_folder_mode_without_ui_denies_everything_but_dns(self):
        policy = kind(render("starrLink.enabled=false", "unpackerr.web.enabled=false"),
                      "NetworkPolicy")["spec"]
        self.assertEqual(policy["ingress"], [])
        self.assertEqual(len(policy["egress"]), 1)
        self.assertEqual({p["port"] for p in policy["egress"][0]["ports"]}, {53})

    def test_catalog_lists_it_as_a_download_client_with_an_open_tile(self):
        annotations = yaml.safe_load((ROOT / CHART / "Chart.yaml").read_text())["annotations"]
        self.assertEqual(annotations["kubarr.io/category"], "download-client")
        self.assertEqual(annotations["kubarr.io/browseable"], "true")
        self.assertEqual(annotations["kubarr.io/system"], "false")





class StarrLinkTests(unittest.TestCase):
    def test_default_follows_the_starr_apps_instead_of_watching_folders(self):
        resources = render()
        env = kind(resources, "ConfigMap")["data"]
        self.assertFalse([k for k in env if k.startswith("UN_FOLDER_")],
                         "watching the folders as well would extract twice")
        deployment = kind(resources, "Deployment")
        self.assertEqual(deployment["metadata"]["annotations"]["kubarr.io/starr-secret"],
                         "kubarr-unpackerr-starr")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertIn({"secretRef": {"name": "kubarr-unpackerr-starr", "optional": True}},
                      container["envFrom"])

    def test_link_off_falls_back_to_folders(self):
        resources = render("starrLink.enabled=false")
        env = kind(resources, "ConfigMap")["data"]
        self.assertEqual(env["UN_FOLDER_qbittorrent_PATH"], "/downloads/qbittorrent")
        deployment = kind(resources, "Deployment")
        self.assertNotIn("annotations", deployment["metadata"])
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertFalse([e for e in container["envFrom"] if "secretRef" in e])

    def test_reaches_only_the_starr_apps_besides_dns(self):
        egress = kind(render(), "NetworkPolicy")["spec"]["egress"]
        namespaces = [peer["namespaceSelector"].get("matchLabels", {}).get("kubernetes.io/metadata.name")
                      for rule in egress for peer in rule["to"] if "namespaceSelector" in peer]
        self.assertEqual(sorted(n for n in namespaces if n), ["lidarr", "radarr", "sonarr"])
        egress_off = kind(render("starrLink.enabled=false"), "NetworkPolicy")["spec"]["egress"]
        self.assertEqual(len(egress_off), 1, "folder mode needs DNS only")

    def test_starr_apps_accept_unpackerr_and_let_kubarr_read_their_key(self):
        for app in ("sonarr", "radarr", "lidarr"):
            with self.subTest(app=app):
                chart = f"media-manager/{app}"
                command = [os.environ.get("HELM", "helm"), "template", app, str(chart_input(ROOT / chart))]
                docs = [d for d in yaml.safe_load_all(subprocess.run(
                    command, check=True, capture_output=True, text=True).stdout) if d]
                policy = next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == app)
                sources = {peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                           for rule in policy["spec"]["ingress"] for peer in rule["from"]
                           if "namespaceSelector" in peer and "matchLabels" in peer["namespaceSelector"]}
                self.assertIn("unpackerr", sources)
                self.assertTrue([d for d in docs if d["kind"] == "RoleBinding"
                                 and d["metadata"]["name"].endswith("-kubarr-config-access")])

    def test_kubarr_owns_the_starr_settings(self):
        result = render("unpackerr.env.UN_RADARR_0_URL=http://x", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set by Kubarr through starrLink", result.stderr)
        result = render("unpackerr.folderWatch=sometimes", check=False)
        self.assertIn("must be auto, true or false", result.stderr)


class WebUiTests(unittest.TestCase):
    def test_ui_is_served_under_the_gateway_path_without_its_own_login(self):
        resources = render()
        env = kind(resources, "ConfigMap")["data"]
        self.assertEqual(env["UN_WEBSERVER_LISTEN_ADDR"], "0.0.0.0:5656")
        self.assertEqual(env["UN_WEBSERVER_URLBASE"], "/unpackerr")
        self.assertEqual(env["UN_WEBSERVER_UI_PASSWORD"], "noauth")
        # noauth trusts nobody without upstreams; the policy narrows it to the gateway.
        self.assertEqual([env[k] for k in sorted(k for k in env if k.startswith("UN_WEBSERVER_UPSTREAMS_"))],
                         ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])
        service = kind(resources, "Service")
        self.assertEqual(service["metadata"]["annotations"]["kubarr.io/base-path"], "/unpackerr")
        self.assertEqual(service["spec"]["ports"][0]["port"], 5656)
        app = kind(resources, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(app["ports"], [{"name": "http", "containerPort": 5656, "protocol": "TCP"}])
        annotations = yaml.safe_load((ROOT / CHART / "Chart.yaml").read_text())["annotations"]
        self.assertEqual(annotations["kubarr.io/browseable"], "true")

    def test_only_the_gateway_may_open_the_ui(self):
        ingress = kind(render(), "NetworkPolicy")["spec"]["ingress"]
        self.assertEqual(ingress, [{"from": [{"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": "openresty"}}}],
            "ports": [{"protocol": "TCP", "port": 5656}]}])

    def test_ui_off_closes_the_server(self):
        resources = render("unpackerr.web.enabled=false")
        self.assertEqual(kind(resources, "ConfigMap")["data"]["UN_WEBSERVER_LISTEN_ADDR"], "")
        self.assertFalse([d for d in resources if d["kind"] == "Service"])
        self.assertEqual(kind(resources, "NetworkPolicy")["spec"]["ingress"], [])
        app = kind(resources, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        self.assertNotIn("ports", app)

    def test_web_settings_come_from_values(self):
        for setting, message in (("unpackerr.env.UN_WEBSERVER_UI_PASSWORD=admin:pw", "set from unpackerr.web"),
                                 ("unpackerr.web.trustedProxies=null", "trustedProxies must list")):
            with self.subTest(setting=setting):
                result = render(setting, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
