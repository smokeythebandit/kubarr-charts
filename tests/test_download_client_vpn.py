"""Render coverage for the shared Gluetun integration in download clients."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CLIENTS = ("deluge", "rutorrent", "sabnzbd", "transmission")
APP_INIT = {"rutorrent": "prepare-runtime-dirs", "transmission": "init-config"}
CALLBACKS = {
    "deluge": 'deluge-console "config --set listen_ports ({{PORT}},{{PORT}})"',
    "transmission": "transmission-remote localhost:9091 -p {{PORT}}",
}


def render(client, overrides=None):
    chart = ROOT / "download-client" / client
    command = [os.environ.get("HELM", "helm"), "template", f"{client}-vpn-test", str(chart_input(chart))]
    with tempfile.TemporaryDirectory() as directory:
        if overrides:
            values_file = Path(directory) / "values.yaml"
            values_file.write_text(yaml.safe_dump(overrides))
            command.extend(["--values", str(values_file)])
        output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    return [item for item in yaml.safe_load_all(output) if item]


def named(items):
    return {item["name"]: item for item in items}


class DownloadClientVpnTests(unittest.TestCase):
    def test_vpn_off_keeps_app_init_and_no_vpn_resources(self):
        for client in CLIENTS:
            with self.subTest(client=client):
                resources = render(client)
                pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
                self.assertNotIn("gluetun", named(pod["containers"]))
                self.assertNotIn("dev-net-tun", named(pod["volumes"]))
                self.assertNotIn("gluetun-tmp", named(pod["volumes"]))
                self.assertEqual(set(named(pod.get("initContainers", []))),
                                 {APP_INIT[client]} if client in APP_INIT else set())
                policy = next(item for item in resources if item["kind"] == "NetworkPolicy")
                self.assertFalse(any(port["port"] == 8001 for rule in policy["spec"]["ingress"]
                                     for port in rule["ports"]))

    def test_vpn_on_uses_private_tmp_and_backend_only_control_ingress(self):
        for client in CLIENTS:
            with self.subTest(client=client):
                resources = render(client, {"vpn": {"enabled": True, "secretName": "vpn-secret",
                                                     "portForwarding": {"enabled": True}}})
                pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
                vpn = named(pod["containers"])["gluetun"]
                environment = {entry["name"]: entry["value"] for entry in vpn["env"]}
                self.assertEqual(vpn["envFrom"], [{"secretRef": {"name": "vpn-secret"}}])
                self.assertEqual(vpn["securityContext"]["capabilities"]["add"], ["NET_ADMIN", "CHOWN"])
                self.assertIn({"name": "gluetun-tmp", "mountPath": "/tmp/gluetun"}, vpn["volumeMounts"])
                self.assertEqual(named(pod["volumes"])["gluetun-tmp"]["emptyDir"], {})
                self.assertEqual(named(pod["volumes"])["dev-net-tun"]["hostPath"]["type"], "CharDevice")
                self.assertEqual(named(pod["initContainers"])["gluetun-tmp-permissions"]["command"],
                                 ["/bin/sh", "-c", "chmod 0700 /tmp/gluetun"])
                if client in APP_INIT:
                    self.assertIn(APP_INIT[client], named(pod["initContainers"]))
                self.assertNotIn("FIREWALL_OUTBOUND_SUBNETS", environment)
                self.assertEqual(environment["FIREWALL"], "on")
                expected_ports = [str(named(pod["containers"])[client]["ports"][0]["containerPort"])]
                self.assertEqual(environment["FIREWALL_INPUT_PORTS"], ",".join(expected_ports + ["9999", "8001"]))
                self.assertEqual(environment.get("VPN_PORT_FORWARDING_UP_COMMAND"), CALLBACKS.get(client))
                self.assertEqual(vpn["livenessProbe"]["httpGet"]["port"], 9999)
                self.assertEqual(vpn["readinessProbe"]["httpGet"]["port"], 9999)
                policies = [item for item in resources if item["kind"] == "NetworkPolicy"]
                self.assertEqual(len(policies), 1)
                ingress = policies[0]["spec"]["ingress"]
                control = [rule for rule in ingress if any(port["port"] == 8001 for port in rule["ports"])]
                self.assertEqual(control, [{
                    "from": [{"namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "kubarr-backend"}},
                        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "kubarr-backend"}}}],
                    "ports": [{"protocol": "TCP", "port": 8001}],
                }])

    def test_transmission_exporter_port_opens_when_enabled(self):
        resources = render("transmission", {"vpn": {"enabled": True}, "exporter": {"enabled": True}})
        pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
        environment = {item["name"]: item["value"] for item in named(pod["containers"])["gluetun"]["env"]}
        self.assertEqual(environment["FIREWALL_INPUT_PORTS"], "9091,19091,9999,8001")

    def test_custom_settings_and_disabled_network_policy(self):
        for client in CLIENTS:
            with self.subTest(client=client):
                resources = render(client, {"vpn": {"enabled": True, "killSwitch": False,
                    "firewallOutboundSubnets": "10.96.0.10/32",
                    "portForwarding": {"enabled": True, "upCommand": "custom {{PORT}}"}},
                    "networkPolicy": {"enabled": False}, "exporter": {"enabled": False}})
                self.assertFalse(any(item["kind"] == "NetworkPolicy" for item in resources))
                pod = next(item for item in resources if item["kind"] == "Deployment")["spec"]["template"]["spec"]
                environment = {item["name"]: item["value"] for item in named(pod["containers"])["gluetun"]["env"]}
                self.assertEqual(environment["FIREWALL_OUTBOUND_SUBNETS"], "10.96.0.10/32")
                self.assertEqual(environment["FIREWALL"], "off")
                self.assertNotIn("FIREWALL_INPUT_PORTS", environment)
                self.assertEqual(environment["VPN_PORT_FORWARDING_UP_COMMAND"], "custom {{PORT}}")


if __name__ == "__main__":
    unittest.main()
