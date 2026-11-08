"""VPN pods run a server picker: an init container chooses a server before
gluetun starts and a sidecar re-evaluates it while the pod runs."""

import json
import os
import subprocess
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input

VPN = ("vpn.enabled=true", "vpn.secretName=vpn-credentials")
TORRENT_CLIENTS = ("download-client/qbittorrent", "download-client/transmission",
                   "download-client/deluge", "download-client/rutorrent")
VPN_CHARTS = TORRENT_CLIENTS + (
    "download-client/sabnzbd", "indexer/jackett", "indexer/prowlarr",
    "media-manager/bazarr", "media-manager/jellyseerr", "media-manager/lidarr",
    "media-manager/radarr", "media-manager/sonarr", "media-server/jellyfin",
    "media-server/plex")


def render(chart, *sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(chart).name,
               str(chart_input(ROOT / chart))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def pod(chart, *sets):
    docs = render(chart, *sets)
    specs = [d["spec"]["template"]["spec"] for d in docs if d["kind"] == "Deployment"]
    # Some charts also render a separate exporter Deployment; the app pod is the
    # one that runs (or would run) gluetun, else the first.
    return next((s for s in specs if "gluetun" in str(s)), specs[0])


def by_name(items, name):
    return next((i for i in items or [] if i["name"] == name), None)


def env(container):
    return {e["name"]: e for e in container.get("env", [])}


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.spec = pod("download-client/qbittorrent", *VPN)

    def test_init_container(self):
        init = by_name(self.spec["initContainers"], "vpn-picker-init")
        self.assertEqual(init["args"], ["init"])
        self.assertEqual(init["securityContext"]["capabilities"]["add"], ["NET_RAW"])
        self.assertEqual(init["securityContext"]["capabilities"]["drop"], ["ALL"])
        # Raw ICMP needs effective NET_RAW, which a non-root user never gets.
        isc = init["securityContext"]
        self.assertEqual((isc["runAsUser"], isc["runAsGroup"]), (0, 0))
        self.assertFalse(isc["runAsNonRoot"])
        self.assertTrue(isc["readOnlyRootFilesystem"])
        self.assertFalse(isc["allowPrivilegeEscalation"])
        self.assertEqual(init["image"], "ghcr.io/smokeythebandit/kubarr-vpn-picker:26e9358")
        self.assertIn({"name": "picker", "mountPath": "/picker"}, init["volumeMounts"])
        names = [c["name"] for c in self.spec["initContainers"]]
        self.assertLess(names.index("vpn-picker-init"), names.index("gluetun-tmp-permissions"))

    def test_sidecar(self):
        sidecar = by_name(self.spec["containers"], "vpn-picker")
        self.assertEqual(sidecar["args"], ["run"])
        sc = sidecar["securityContext"]
        self.assertNotIn("add", sc["capabilities"])
        self.assertEqual(sc["capabilities"]["drop"], ["ALL"])
        self.assertTrue(sc["readOnlyRootFilesystem"])
        self.assertTrue(sc["runAsNonRoot"])
        self.assertFalse(sc["allowPrivilegeEscalation"])
        self.assertEqual((sc["runAsUser"], sc["runAsGroup"]), (1000, 1000))
        self.assertIn({"name": "picker", "mountPath": "/picker"}, sidecar["volumeMounts"])
        init_sc = by_name(self.spec["initContainers"], "vpn-picker-init")["securityContext"]
        self.assertEqual((init_sc["runAsUser"], init_sc["runAsGroup"]), (0, 0))
        self.assertTrue(init_sc["readOnlyRootFilesystem"])

    def test_shared_volume(self):
        self.assertEqual(by_name(self.spec["volumes"], "picker")["emptyDir"], {})

    def test_env_and_secret_isolation(self):
        for container in (by_name(self.spec["initContainers"], "vpn-picker-init"),
                          by_name(self.spec["containers"], "vpn-picker")):
            self.assertNotIn("envFrom", container)
            e = env(container)
            provider = e["VPN_SERVICE_PROVIDER"]["valueFrom"]["secretKeyRef"]
            self.assertEqual(provider, {"name": "vpn-credentials",
                                        "key": "VPN_SERVICE_PROVIDER", "optional": True})
            for key in ("VPN_TYPE", "SERVER_COUNTRIES", "SERVER_CITIES", "SERVER_HOSTNAMES"):
                self.assertTrue(e[key]["valueFrom"]["secretKeyRef"]["optional"])
            self.assertNotIn("WIREGUARD_PRIVATE_KEY", e)
            self.assertEqual(e["PICKER_STATE_DIR"]["value"], "/picker")
            self.assertEqual(e["VPN_PORT_FORWARDING"]["value"], "off")
            self.assertEqual(e["PICKER_SHORTLIST"]["value"], "5")
            self.assertEqual(e["PICKER_SAMPLE"]["value"], "40")
            self.assertEqual(e["PICKER_INIT_BUDGET"]["value"], "60s")
            self.assertEqual(e["PICKER_TEST_SECONDS"]["value"], "8")
            self.assertEqual(e["PICKER_TEST_ENDPOINTS"]["value"],
                             "https://proof.ovh.net/files/100Mb.dat,http://speedtest.tele2.net/100MB.zip")
            self.assertEqual(e["PICKER_INTERVAL"]["value"], "15m")
            self.assertEqual(e["PICKER_DEGRADE_THRESHOLD"]["value"], "0.4")
            self.assertEqual(e["PICKER_DEGRADE_CHECKS"]["value"], "3")
            self.assertEqual(e["PICKER_COOLDOWN"]["value"], "6h")
            self.assertEqual(e["PICKER_SWITCH_WHILE_RUNNING"]["value"], "true")

    def test_port_forwarding_flag(self):
        spec = pod("download-client/qbittorrent", *VPN, "vpn.portForwarding.enabled=true")
        e = env(by_name(spec["containers"], "vpn-picker"))
        self.assertEqual(e["VPN_PORT_FORWARDING"]["value"], "on")

    def test_gluetun_control_api_auth(self):
        e = env(by_name(self.spec["containers"], "gluetun"))
        self.assertEqual(json.loads(e["HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE"]["value"]),
                         {"auth": "none"})
        self.assertEqual(e["HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE"]["value"], '{"auth":"none"}')

    def test_p2p_only_for_torrent_clients(self):
        for chart in VPN_CHARTS:
            spec = pod(chart, *VPN)
            expected = "true" if chart in TORRENT_CLIENTS else "false"
            for container in (by_name(spec["initContainers"], "vpn-picker-init"),
                              by_name(spec["containers"], "vpn-picker")):
                self.assertEqual(env(container)["PICKER_P2P"]["value"], expected, chart)

    def test_values_override(self):
        spec = pod("download-client/qbittorrent", *VPN, "vpn.picker.shortlist=7",
                   "vpn.picker.switchWhileRunning=false")
        e = env(by_name(spec["containers"], "vpn-picker"))
        self.assertEqual(e["PICKER_SHORTLIST"]["value"], "7")
        self.assertEqual(e["PICKER_SWITCH_WHILE_RUNNING"]["value"], "false")

    def assert_no_picker(self, spec, auth_env=False):
        self.assertIsNone(by_name(spec.get("initContainers"), "vpn-picker-init"))
        self.assertIsNone(by_name(spec["containers"], "vpn-picker"))
        self.assertIsNone(by_name(spec.get("volumes"), "picker"))
        gluetun = by_name(spec["containers"], "gluetun")
        if gluetun:
            self.assertNotIn("HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE", env(gluetun))

    def test_picker_disabled(self):
        spec = pod("download-client/qbittorrent", *VPN, "vpn.picker.enabled=false")
        self.assertIsNotNone(by_name(spec["containers"], "gluetun"))
        self.assert_no_picker(spec)

    def test_kill_switch_off_disables_picker(self):
        spec = pod("download-client/qbittorrent", *VPN, "vpn.killSwitch=false")
        self.assertIsNotNone(by_name(spec["containers"], "gluetun"))
        self.assert_no_picker(spec)

    def test_vpn_disabled(self):
        spec = pod("download-client/qbittorrent")
        self.assertIsNone(by_name(spec["containers"], "gluetun"))
        self.assert_no_picker(spec)

    def test_reuse_values_without_picker_key(self):
        # helm upgrade --reuse-values drops new chart defaults, so vpn.picker is absent.
        spec = pod("download-client/qbittorrent", *VPN, "vpn.picker=null")
        e = env(by_name(spec["containers"], "vpn-picker"))
        self.assertEqual(e["PICKER_SHORTLIST"]["value"], "5")
        self.assertEqual(e["PICKER_P2P"]["value"], "true")
        self.assertIsNotNone(by_name(spec["initContainers"], "vpn-picker-init"))
        self.assertIn("HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE",
                      env(by_name(spec["containers"], "gluetun")))


if __name__ == "__main__":
    unittest.main()
