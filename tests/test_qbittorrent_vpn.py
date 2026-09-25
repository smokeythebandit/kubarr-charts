"""Render-level coverage for qBittorrent's optional Gluetun sidecar."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "download-client/qbittorrent"


def render_resources(overrides=None):
    command = [os.environ.get("HELM", "helm"), "template", "qbittorrent-vpn-test", str(chart_input(CHART))]
    with tempfile.TemporaryDirectory() as directory:
        if overrides:
            values_file = Path(directory) / "values.yaml"
            values_file.write_text(yaml.safe_dump(overrides))
            command.extend(["--values", str(values_file)])
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def render(overrides=None):
    return next(resource for resource in render_resources(overrides) if resource["kind"] == "Deployment")


def by_name(items):
    return {item["name"]: item for item in items}


def env(container):
    return {item["name"]: item["value"] for item in container["env"]}


class QBittorrentVpnTests(unittest.TestCase):
    def test_control_policy_only_allows_backend_on_8001_when_vpn_enabled(self):
        resources = render_resources({"vpn": {"enabled": True}})
        policies = [resource for resource in resources if resource["kind"] == "NetworkPolicy"]
        self.assertEqual(len(policies), 2)
        base = next(policy for policy in policies if policy["metadata"]["name"] == "qbittorrent-vpn-test")
        control = next(policy for policy in policies if policy["metadata"]["name"] == "qbittorrent-vpn-test-gluetun-control")
        self.assertEqual(control["metadata"]["namespace"], "qbittorrent")
        deployment = next(resource for resource in resources if resource["kind"] == "Deployment")
        self.assertEqual(control["spec"]["podSelector"], base["spec"]["podSelector"])
        self.assertEqual(control["spec"]["podSelector"]["matchLabels"],
                         deployment["spec"]["template"]["metadata"]["labels"])
        self.assertEqual(control["spec"]["policyTypes"], ["Ingress"])
        self.assertNotIn("egress", control["spec"])
        self.assertEqual(control["spec"]["ingress"], [{
            "from": [{
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kubarr-backend"}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "kubarr-backend"}},
            }],
            "ports": [{"protocol": "TCP", "port": 8001}],
        }])
        self.assertNotIn(8001, [port["port"] for rule in base["spec"]["ingress"]
                                for port in rule["ports"]])

    def test_control_policy_absent_without_vpn_or_network_policy(self):
        for overrides, expected_count in [({}, 1),
                                          ({"vpn": {"enabled": False}}, 1),
                                          ({"vpn": {"enabled": True}, "networkPolicy": {"enabled": False}}, 0)]:
            with self.subTest(overrides=overrides):
                policies = [resource for resource in render_resources(overrides)
                            if resource["kind"] == "NetworkPolicy"]
                self.assertEqual(len(policies), expected_count)

    def test_vpn_disabled_has_no_gluetun_resources(self):
        pod = render()["spec"]["template"]["spec"]
        self.assertNotIn("gluetun", by_name(pod["containers"]))
        self.assertIn("qbittorrent", by_name(pod["containers"]))
        self.assertIn("exporter", by_name(pod["containers"]))
        self.assertNotIn("initContainers", pod)
        self.assertNotIn("gluetun-tmp", by_name(pod["volumes"]))
        self.assertNotIn("dev-net-tun", by_name(pod["volumes"]))

    def test_vpn_mount_permissions_security_and_default_egress(self):
        pod = render({"vpn": {"enabled": True, "secretName": "vpn-credentials"}})["spec"]["template"]["spec"]
        containers = by_name(pod["containers"])
        gluetun = containers["gluetun"]
        volumes = by_name(pod["volumes"])
        init = by_name(pod["initContainers"])["gluetun-tmp-permissions"]

        self.assertEqual(gluetun["image"], "qmcgaw/gluetun:v3.41.3")
        self.assertEqual(gluetun["envFrom"], [{"secretRef": {"name": "vpn-credentials"}}])
        self.assertEqual(gluetun["securityContext"], {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"], "add": ["NET_ADMIN", "CHOWN"]},
        })
        self.assertEqual(volumes["dev-net-tun"]["hostPath"], {
            "path": "/dev/net/tun", "type": "CharDevice",
        })
        self.assertEqual(volumes["gluetun-tmp"]["emptyDir"], {})
        self.assertIn({"name": "gluetun-tmp", "mountPath": "/tmp/gluetun"}, gluetun["volumeMounts"])
        self.assertEqual(init["command"], ["/bin/sh", "-c", "chmod 0700 /tmp/gluetun"])
        self.assertEqual(init["image"], gluetun["image"])
        self.assertEqual(init["securityContext"], {
            "runAsUser": 0, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]},
        })
        self.assertEqual(init["volumeMounts"], [{"name": "gluetun-tmp", "mountPath": "/tmp/gluetun"}])
        self.assertNotIn("gluetun-tmp", [mount["name"] for mount in containers["qbittorrent"]["volumeMounts"]])
        self.assertNotIn("FIREWALL_OUTBOUND_SUBNETS", env(gluetun))
        self.assertEqual(env(gluetun)["FIREWALL"], "on")
        self.assertEqual(env(gluetun)["FIREWALL_INPUT_PORTS"], "8080,8000,9999,8001")
        self.assertNotIn("VPN_PORT_FORWARDING_UP_COMMAND", env(gluetun))

    def test_explicit_subnet_and_kill_switch_override(self):
        gluetun = by_name(render({"vpn": {
            "enabled": True, "killSwitch": False, "firewallOutboundSubnets": "10.96.0.10/32",
        }})["spec"]["template"]["spec"]["containers"])["gluetun"]
        self.assertEqual(env(gluetun)["FIREWALL_OUTBOUND_SUBNETS"], "10.96.0.10/32")
        self.assertEqual(env(gluetun)["FIREWALL"], "off")
        self.assertNotIn("FIREWALL_INPUT_PORTS", env(gluetun))

    def test_port_forwarding_posts_with_retry_to_configured_webui_port(self):
        gluetun = by_name(render({"vpn": {"enabled": True, "portForwarding": {"enabled": True}},
                                   "qbittorrent": {"service": {"targetPort": 18080}}})
                          ["spec"]["template"]["spec"]["containers"])["gluetun"]
        command = env(gluetun)["VPN_PORT_FORWARDING_UP_COMMAND"]
        self.assertEqual(env(gluetun)["VPN_PORT_FORWARDING"], "on")
        self.assertEqual(env(gluetun)["PORT_FORWARD_ONLY"], "on")
        self.assertIn('wget -q -T 5 -O /dev/null --post-data="json={\\"listen_port\\":{{PORT}}}"', command)
        self.assertIn("http://127.0.0.1:18080/api/v2/app/setPreferences", command)
        self.assertIn('until wget', command)
        self.assertIn('[ "$i" -lt 30 ] || exit 1; sleep 2', command)
        self.assertNotIn("?json=", command)
        self.assertNotIn("VPN_PORT_FORWARDING_DOWN_COMMAND", env(gluetun))

    def test_custom_up_command_is_preserved(self):
        custom = "/bin/sh -c 'my-hook {{PORT}}'"
        gluetun = by_name(render({"vpn": {"enabled": True, "portForwarding": {
            "enabled": True, "upCommand": custom,
        }}})["spec"]["template"]["spec"]["containers"])["gluetun"]
        self.assertEqual(env(gluetun)["VPN_PORT_FORWARDING_UP_COMMAND"], custom)

    def test_callback_retries_failed_post_then_sends_listen_port(self):
        gluetun = by_name(render({"vpn": {"enabled": True, "portForwarding": {"enabled": True}}})
                          ["spec"]["template"]["spec"]["containers"])["gluetun"]
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(503 if len(requests) == 1 else 200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            command = env(gluetun)["VPN_PORT_FORWARDING_UP_COMMAND"]
            command = command.replace("{{PORT}}", "51413").replace(":8080/", f":{server.server_port}/")
            subprocess.run(command, shell=True, check=True, capture_output=True, text=True, timeout=15)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(requests, [
            ("/api/v2/app/setPreferences", b'json={"listen_port":51413}'),
            ("/api/v2/app/setPreferences", b'json={"listen_port":51413}'),
        ])


if __name__ == "__main__":
    unittest.main()
