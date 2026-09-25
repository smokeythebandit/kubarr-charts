"""VPN-on/off render coverage for media and indexer charts."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHARTS = {
    "indexer/jackett": ("jackett", 9117, None),
    "media-manager/jellyseerr": ("jellyseerr", 5055, None),
    "media-manager/radarr": ("radarr", 7878, 9708),
    "media-manager/sonarr": ("sonarr", 8989, 9707),
    "media-server/jellyfin": ("jellyfin", 8096, None),
    "media-server/plex": ("plex", 32400, None),
}


def render(chart, enabled, extra=None, app_values=None, network_policy=None, exporter=None):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        overrides = {"vpn": {"enabled": enabled, "secretName": "vpn-credentials", **(extra or {})},
                     CHARTS[chart][0]: app_values or {}}
        if network_policy is not None:
            overrides["networkPolicy"] = network_policy
        if exporter is not None:
            overrides["exporter"] = exporter
        values.write_text(yaml.safe_dump(overrides))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "media-vpn-test", str(chart_input(ROOT / chart)), "-f", str(values)],
            capture_output=True, text=True, check=True,
        )
    resources = [item for item in yaml.safe_load_all(result.stdout) if item]
    deployment = next(item for item in resources if item["kind"] == "Deployment" and
                      any(c["name"] == CHARTS[chart][0] for c in item["spec"]["template"]["spec"]["containers"]))
    policy = next(item for item in resources if item["kind"] == "NetworkPolicy" and
                  item["metadata"]["namespace"] == deployment["metadata"]["namespace"])
    return deployment["spec"]["template"]["spec"], policy["spec"]


class MediaVpnTests(unittest.TestCase):
    def test_exporter_ingress_is_scoped_and_independent_of_vpn(self):
        for chart, (app, port, exporter_port) in CHARTS.items():
            for vpn_enabled in (False, True):
                with self.subTest(chart=chart, vpn_enabled=vpn_enabled):
                    _, policy = render(chart, vpn_enabled)
                    exporter_rules = [rule for rule in policy["ingress"]
                                      if any("podSelector" in peer and
                                             peer["podSelector"]["matchLabels"].get("app.kubernetes.io/name") == f"{app}-exporter"
                                             for peer in rule["from"])]
                    if chart in ("media-manager/sonarr", "media-manager/radarr"):
                        self.assertEqual(exporter_rules, [{
                            "from": [{"podSelector": {"matchLabels": {
                                "app.kubernetes.io/name": f"{app}-exporter",
                                "app.kubernetes.io/instance": "media-vpn-test",
                            }}}],
                            "ports": [{"protocol": "TCP", "port": port}],
                        }])
                        self.assertNotIn("namespaceSelector", exporter_rules[0]["from"][0])
                    else:
                        self.assertEqual(exporter_rules, [])
                        _, opted_in = render(chart, vpn_enabled,
                                             network_policy={"allowExporterIngress": True})
                        self.assertEqual(opted_in["ingress"], policy["ingress"])

    def test_exporter_ingress_requires_both_opt_in_and_exporter(self):
        for chart in ("media-manager/sonarr", "media-manager/radarr"):
            with self.subTest(chart=chart):
                _, baseline = render(chart, False)
                for overrides in ({"network_policy": {"allowExporterIngress": False}},
                                  {"exporter": {"enabled": False}}):
                    _, policy = render(chart, False, **overrides)
                    self.assertEqual(len(policy["ingress"]), len(baseline["ingress"]) - 1)
                    self.assertFalse(any("podSelector" in peer for rule in policy["ingress"]
                                         for peer in rule["from"]))

    def test_exporter_ingress_uses_app_target_port(self):
        _, policy = render("media-manager/sonarr", False,
                           app_values={"service": {"targetPort": 12345}})
        self.assertEqual(policy["ingress"][-1]["ports"], [{"protocol": "TCP", "port": 12345}])

    def test_on_off_all_charts(self):
        for chart, (app, port, exporter) in CHARTS.items():
            with self.subTest(chart=chart):
                off, off_policy = render(chart, False)
                on, on_policy = render(chart, True)
                off_names = {c["name"] for c in off["containers"]}
                self.assertEqual(off_names, {app})
                self.assertNotIn("gluetun-tmp", {v["name"] for v in off["volumes"]})
                self.assertNotIn("gluetun-tmp-permissions", {c["name"] for c in off.get("initContainers", [])})
                self.assertEqual(len(on_policy["ingress"]), len(off_policy["ingress"]) + 1)
                control = on_policy["ingress"][-1]
                self.assertEqual(control["ports"], [{"protocol": "TCP", "port": 8001}])
                self.assertEqual(control["from"], [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kubarr-backend"}},
                                                   "podSelector": {"matchLabels": {"app.kubernetes.io/name": "kubarr-backend"}}}])
                containers = {c["name"]: c for c in on["containers"]}
                self.assertEqual(set(containers), {app, "gluetun"})
                gluetun = containers["gluetun"]
                env = {entry["name"]: entry["value"] for entry in gluetun["env"]}
                self.assertNotIn("FIREWALL_OUTBOUND_SUBNETS", env)
                self.assertEqual(env["FIREWALL"], "on")
                self.assertEqual(env["FIREWALL_INPUT_PORTS"], ",".join(map(str, [port] + ([exporter] if exporter else []) + [9999, 8001])))
                self.assertEqual(gluetun["securityContext"]["capabilities"]["add"], ["NET_ADMIN", "CHOWN"])
                self.assertIn({"name": "gluetun-tmp", "emptyDir": {}}, on["volumes"])
                self.assertIn({"name": "gluetun-tmp", "mountPath": "/tmp/gluetun"}, gluetun["volumeMounts"])
                self.assertIn("gluetun-tmp-permissions", {c["name"] for c in on["initContainers"]})
                if chart in ("media-manager/sonarr", "media-manager/radarr") or chart == "indexer/jackett":
                    self.assertIn("init-config", {c["name"] for c in off["initContainers"]})
                    self.assertIn("init-config", {c["name"] for c in on["initContainers"]})

    def test_forwarding_callback_and_jackett_without_base_path(self):
        pod, _ = render("indexer/jackett", True,
                        {"portForwarding": {"enabled": True, "upCommand": "custom callback"}},
                        {"basePath": ""})
        self.assertEqual([c["name"] for c in pod["initContainers"]], ["gluetun-tmp-permissions"])
        env = {e["name"]: e["value"] for c in pod["containers"] if c["name"] == "gluetun" for e in c["env"]}
        self.assertEqual(env["VPN_PORT_FORWARDING_UP_COMMAND"], "custom callback")
        self.assertEqual(env["VPN_PORT_FORWARDING"], "on")
        off, _ = render("indexer/jackett", False, app_values={"basePath": ""})
        self.assertNotIn("initContainers", off)


if __name__ == "__main__":
    unittest.main()
