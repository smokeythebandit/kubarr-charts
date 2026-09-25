"""Render-level coverage for Sonarr's optional Gluetun sidecar."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "media-manager/sonarr"


def render(overrides=None):
    command = [
        os.environ.get("HELM", "helm"),
        "template",
        "sonarr-vpn-test",
        str(chart_input(CHART)),
        "--set",
        "vpn.enabled=true",
        "--set",
        "vpn.secretName=sonarr-vpn-credentials",
    ]
    with tempfile.TemporaryDirectory() as directory:
        if overrides:
            values_file = Path(directory) / "values.yaml"
            values_file.write_text(yaml.safe_dump(overrides))
            command.extend(["--values", str(values_file)])
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )

    resources = [resource for resource in yaml.safe_load_all(result.stdout) if resource]
    return next(
        resource
        for resource in resources
        if resource["kind"] == "Deployment"
        and any(
            container["name"] == "sonarr"
            for container in resource["spec"]["template"]["spec"]["containers"]
        )
    )


def containers(deployment):
    return {
        container["name"]: container
        for container in deployment["spec"]["template"]["spec"]["containers"]
    }


def environment(container):
    return {entry["name"]: entry["value"] for entry in container["env"]}


class SonarrVpnTests(unittest.TestCase):
    def test_enabled_defaults_preserve_vpn_security_and_networking(self):
        deployment = render()
        gluetun = containers(deployment)["gluetun"]

        self.assertEqual(
            gluetun["envFrom"],
            [{"secretRef": {"name": "sonarr-vpn-credentials"}}],
        )
        self.assertEqual(
            gluetun["securityContext"],
            {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"], "add": ["NET_ADMIN", "CHOWN"]},
            },
        )
        tun_volume = next(
            volume
            for volume in deployment["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "dev-net-tun"
        )
        self.assertEqual(
            tun_volume["hostPath"],
            {"path": "/dev/net/tun", "type": "CharDevice"},
        )
        self.assertEqual(
            gluetun["volumeMounts"],
            [
                {"name": "dev-net-tun", "mountPath": "/dev/net/tun"},
                {"name": "gluetun-tmp", "mountPath": "/tmp/gluetun"},
            ],
        )
        pod = deployment["spec"]["template"]["spec"]
        self.assertIn({"name": "gluetun-tmp", "emptyDir": {}}, pod["volumes"])
        self.assertEqual(
            {container["name"] for container in pod["initContainers"]},
            {"init-config", "gluetun-tmp-permissions"},
        )
        permissions = next(c for c in pod["initContainers"] if c["name"] == "gluetun-tmp-permissions")
        self.assertEqual(permissions["command"], ["/bin/sh", "-c", "chmod 0700 /tmp/gluetun"])

        env = environment(gluetun)
        self.assertEqual(env["FIREWALL"], "on")
        self.assertNotIn("FIREWALL_OUTBOUND_SUBNETS", env)
        self.assertEqual(env["FIREWALL_INPUT_PORTS"], "8989,9707,9999,8001")
        self.assertEqual(env["HEALTH_SERVER_ADDRESS"], ":9999")
        self.assertEqual(env["HTTP_CONTROL_SERVER_ADDRESS"], ":8001")
        self.assertEqual(
            gluetun["ports"],
            [
                {"name": "gluetun-http", "containerPort": 8888, "protocol": "TCP"},
                {"name": "gluetun-ctrl", "containerPort": 8001, "protocol": "TCP"},
            ],
        )
        self.assertEqual(gluetun["livenessProbe"]["httpGet"]["port"], 9999)
        self.assertEqual(gluetun["readinessProbe"]["httpGet"]["port"], 9999)

    def test_kill_switch_can_be_disabled_without_changing_other_defaults(self):
        gluetun = containers(render({"vpn": {"killSwitch": False}}))["gluetun"]
        env = environment(gluetun)

        self.assertEqual(env["FIREWALL"], "off")
        self.assertNotIn("FIREWALL_INPUT_PORTS", env)
        self.assertNotIn("FIREWALL_OUTBOUND_SUBNETS", env)
        self.assertEqual(env["HEALTH_SERVER_ADDRESS"], ":9999")
        self.assertEqual(env["HTTP_CONTROL_SERVER_ADDRESS"], ":8001")

    def test_explicit_outbound_subnet_and_exporter_toggle(self):
        gluetun = containers(render({"vpn": {"firewallOutboundSubnets": "10.42.0.0/24"}, "exporter": {"enabled": False}}))["gluetun"]
        env = environment(gluetun)
        self.assertEqual(env["FIREWALL_OUTBOUND_SUBNETS"], "10.42.0.0/24")
        self.assertEqual(env["FIREWALL_INPUT_PORTS"], "8989,9999,8001")

    def test_extra_env_is_appended_to_gluetun_only(self):
        extra_env = [
            {"name": "HEALTH_TARGET_ADDRESS", "value": "1.1.1.1:443"},
            {"name": "PUBLICIP_API", "value": "https://public.example/ip"},
            {"name": "DOT", "value": "off"},
        ]
        rendered_containers = containers(render({"vpn": {"extraEnv": extra_env}}))
        gluetun_env = rendered_containers["gluetun"]["env"]

        self.assertEqual(gluetun_env[-len(extra_env) :], extra_env)
        self.assertNotIn("env", rendered_containers["sonarr"])

    def test_extra_env_defaults_are_absent(self):
        env_names = [entry["name"] for entry in containers(render())["gluetun"]["env"]]

        self.assertNotIn("HEALTH_TARGET_ADDRESS", env_names)
        self.assertNotIn("PUBLICIP_API", env_names)
        self.assertNotIn("DOT", env_names)


if __name__ == "__main__":
    unittest.main()
