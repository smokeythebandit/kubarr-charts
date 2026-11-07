"""Sonarr and Radarr switch themselves on in Bazarr through a key Kubarr shares.

Kubarr puts one API key in a Secret in each namespace and passes its name as
bazarrIntegration.secretName. Bazarr's init container writes the key under
auth.apikey in config.yaml; each manager's setup-bazarr service then turns
itself on in Bazarr's settings with its own address and API key.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


SECRET = "kubarr-bazarr-api-key"
KEY = "0123456789abcdef0123456789abcdef"
SONARR_KEY = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BAZARR = "media-manager/bazarr"
ARRS = {
    "media-manager/sonarr": ("sonarr.sonarr.svc.cluster.local", "8989"),
    "media-manager/radarr": ("radarr.radarr.svc.cluster.local", "7878"),
}


def render(chart, *sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(chart).name,
               str(chart_input(ROOT / chart))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def enabled(chart):
    return render(chart, f"bazarrIntegration.secretName={SECRET}")


def pod(resources, name):
    for doc in resources:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == name:
            return doc["spec"]["template"]
    raise AssertionError(f"no Deployment {name}")


def by_name(items, name):
    return next(item for item in items if item["name"] == name)


def secret_volumes(spec):
    return [v for v in spec.get("volumes", [])
            if v.get("secret", {}).get("secretName") == SECRET]


def mounts(container):
    return {m["mountPath"]: m for m in container.get("volumeMounts", [])}


def setup_script(resources):
    for doc in resources:
        if doc["kind"] == "ConfigMap" and "setup-bazarr.sh" in doc.get("data", {}):
            return doc["data"]["setup-bazarr.sh"]
    raise AssertionError("no setup-bazarr.sh")


class BazarrKeyTests(unittest.TestCase):
    def test_nothing_is_mounted_without_a_key_from_kubarr(self):
        spec = pod(render(BAZARR), "bazarr")["spec"]
        self.assertEqual(secret_volumes(spec), [])

    def test_only_init_config_reads_the_key_and_read_only(self):
        spec = pod(enabled(BAZARR), "bazarr")["spec"]
        self.assertEqual(len(secret_volumes(spec)), 1)
        init = by_name(spec["initContainers"], "init-config")
        self.assertIs(mounts(init)["/kubarr-secrets/bazarr"].get("readOnly"), True)
        self.assertNotIn("/kubarr-secrets/bazarr", mounts(by_name(spec["containers"], "bazarr")))

    def test_pod_restarts_when_the_link_changes(self):
        def annotations(resources):
            return pod(resources, "bazarr")["metadata"]["annotations"]
        self.assertNotEqual(annotations(render(BAZARR)), annotations(enabled(BAZARR)))

    def run_init(self, existing_config):
        spec = pod(enabled(BAZARR), "bazarr")["spec"]
        script = by_name(spec["initContainers"], "init-config")["command"][-1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "secret").mkdir()
            (tmp / "secret" / "api-key").write_text(KEY + "\n")
            (tmp / "config" / "config").mkdir(parents=True)
            config_file = tmp / "config" / "config" / "config.yaml"
            if existing_config is not None:
                config_file.write_text(existing_config)
            script = (script.replace("/kubarr-secrets/bazarr", str(tmp / "secret"))
                      .replace('"/config/config/config.yaml"', f'"{config_file}"')
                      .replace("mkdir -p /config/config", f"mkdir -p {tmp}/config/config"))
            self.assertNotIn(" /config", script)
            result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return yaml.safe_load(config_file.read_text()), result.stdout

    def test_fresh_config_gets_kubarrs_key(self):
        config, log = self.run_init(None)
        self.assertEqual(config["auth"]["apikey"], KEY)
        self.assertEqual(config["general"]["base_url"], "/bazarr")
        self.assertNotIn(KEY, log)

    def test_only_the_auth_key_is_replaced(self):
        old = "fedcba9876543210fedcba9876543210"
        config, log = self.run_init(
            "general:\n  base_url: /bazarr\n  use_sonarr: true\n"
            f"auth:\n  apikey: {old}\n  type: null\n"
            f"sonarr:\n  apikey: {SONARR_KEY}\n  ip: 127.0.0.1\n")
        self.assertEqual(config["auth"], {"apikey": KEY, "type": None})
        self.assertEqual(config["sonarr"]["apikey"], SONARR_KEY)
        self.assertIs(config["general"]["use_sonarr"], True)
        for secret in (KEY, old, SONARR_KEY):
            self.assertNotIn(secret, log)

    def test_only_bazarrs_own_base_url_is_pinned(self):
        config, _ = self.run_init(
            "general:\n  base_url: /old\n  port: 6767\n"
            "sonarr:\n  base_url: /sonarr\nradarr:\n  base_url: /radarr\n")
        self.assertEqual(config["general"]["base_url"], "/bazarr")
        self.assertEqual(config["sonarr"]["base_url"], "/sonarr")
        self.assertEqual(config["radarr"]["base_url"], "/radarr")

    def test_general_without_a_base_url_gains_one(self):
        config, _ = self.run_init("general:\n  port: 6767\nsonarr:\n  base_url: /sonarr\n")
        self.assertEqual(config["general"]["base_url"], "/bazarr")
        self.assertEqual(config["sonarr"]["base_url"], "/sonarr")

    def test_auth_section_without_a_key_gains_one(self):
        config, _ = self.run_init("auth:\n  type: null\nsonarr:\n  ip: 127.0.0.1\n")
        self.assertEqual(config["auth"], {"type": None, "apikey": KEY})
        self.assertEqual(config["sonarr"], {"ip": "127.0.0.1"})

    def test_auth_section_last_in_the_file(self):
        config, _ = self.run_init("general:\n  port: 6767\nauth:\n  type: null\n")
        self.assertEqual(config["auth"]["apikey"], KEY)


class ManagerConnectionTests(unittest.TestCase):
    def test_key_mounts_only_with_a_key_from_kubarr(self):
        for chart in ARRS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                self.assertEqual(secret_volumes(pod(render(chart), name)["spec"]), [])
                spec = pod(enabled(chart), name)["spec"]
                self.assertEqual(len(secret_volumes(spec)), 1)
                app = mounts(by_name(spec["containers"], name))
                self.assertIs(app["/kubarr-secrets/bazarr"].get("readOnly"), True)
                self.assertIn("/custom-services.d/setup-bazarr", app)

    def test_script_connects_this_manager_at_its_own_service(self):
        for chart, (host, port) in ARRS.items():
            name = Path(chart).name
            with self.subTest(chart=chart):
                script = setup_script(render(chart))
                self.assertIn(f'SECTION="{name}"', script)
                self.assertIn(f'SELF_HOST="{host}"', script)
                self.assertIn(f'SELF_PORT="{port}"', script)
                self.assertIn("bazarr.bazarr.svc.cluster.local:6767/bazarr", script)
                syntax = subprocess.run(["bash", "-n"], input=script,
                                        capture_output=True, text=True)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def state(self, chart, settings):
        script = setup_script(render(chart))
        start = script.index("state=$(echo")
        expr = script[start:script.index("')", start) + 2]
        name = Path(chart).name
        host, port = ARRS[chart]
        prelude = (f'SECTION={name}; SELF_HOST={host}; SELF_PORT={port}; '
                   f'SELF_BASE=/{name}; own_key={SONARR_KEY}; settings=$(cat)\n')
        result = subprocess.run(["bash", "-c", prelude + expr + '\necho "$state"'],
                                input=json.dumps(settings),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_decides_when_to_connect(self):
        chart = "media-manager/sonarr"
        mine = {"ip": "sonarr.sonarr.svc.cluster.local", "port": 8989,
                "base_url": "/sonarr", "apikey": SONARR_KEY}
        cases = [
            ({"general": {"use_sonarr": False}, "sonarr": {"ip": "127.0.0.1"}}, "missing"),
            ({"general": {"use_sonarr": True}, "sonarr": mine}, "ours"),
            ({"general": {"use_sonarr": True}, "sonarr": {**mine, "apikey": "stale"}}, "missing"),
            ({"general": {"use_sonarr": True}, "sonarr": {**mine, "ip": "10.0.0.5"}}, "foreign"),
        ]
        for settings, expected in cases:
            with self.subTest(expected=expected, settings=settings):
                self.assertEqual(self.state(chart, settings), expected)

    def test_script_stays_idle_without_a_key(self):
        script = setup_script(render("media-manager/sonarr"))
        guard = script.index('if [ ! -s "$KEY_FILE" ]')
        self.assertLess(guard, script.index("-X POST"))
        self.assertIn("exec sleep infinity", script[guard:guard + 200])

    def test_reserved_paths_cover_the_new_mounts(self):
        for chart in ARRS:
            with self.subTest(chart=chart):
                values = yaml.safe_load((ROOT / chart / "values.yaml").read_text())
                reserved = values["kubarr"]["storage"]["reserved"]["main"]
                self.assertIn("/custom-services.d/setup-bazarr", reserved)
                self.assertIn("/kubarr-secrets/bazarr", reserved)

    def test_network_policies_allow_both_directions(self):
        bazarr = yaml.safe_load((ROOT / BAZARR / "values.yaml").read_text())["networkPolicy"]
        for chart in ARRS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                policy = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["networkPolicy"]
                self.assertIn("bazarr", policy["egressTo"])
                self.assertIn("bazarr", policy["ingressFrom"])
                self.assertIn(name, bazarr["egressTo"])
                self.assertIn(name, bazarr["ingressFrom"])


if __name__ == "__main__":
    unittest.main()
