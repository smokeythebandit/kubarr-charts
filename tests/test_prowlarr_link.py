"""Sonarr, Radarr and Lidarr register with Prowlarr through a key Kubarr shares.

Kubarr puts one API key in a Secret in each namespace and passes its name as
prowlarrIntegration.secretName. Prowlarr's init container writes the key into
config.xml; each manager's setup-prowlarr service registers the manager as a
Prowlarr application with it, so Prowlarr pushes its indexers to the manager.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


SECRET = "kubarr-prowlarr-api-key"
KEY = "0123456789abcdef0123456789abcdef"
PROWLARR = "indexer/prowlarr"
ARRS = {
    "media-manager/sonarr": ("Sonarr", "http://sonarr.sonarr.svc.cluster.local:8989/sonarr"),
    "media-manager/radarr": ("Radarr", "http://radarr.radarr.svc.cluster.local:7878/radarr"),
    "media-manager/lidarr": ("Lidarr", "http://lidarr.lidarr.svc.cluster.local:8686/lidarr"),
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
    return render(chart, f"prowlarrIntegration.secretName={SECRET}")


def pod(resources, name):
    for doc in resources:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == name:
            return doc["spec"]["template"]["spec"]
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
        if doc["kind"] == "ConfigMap" and "setup-prowlarr.sh" in doc.get("data", {}):
            return doc["data"]["setup-prowlarr.sh"]
    raise AssertionError("no setup-prowlarr.sh")


class ProwlarrKeyTests(unittest.TestCase):
    def test_nothing_is_mounted_without_a_key_from_kubarr(self):
        spec = pod(render(PROWLARR), "prowlarr")
        self.assertEqual(secret_volumes(spec), [])
        init = by_name(spec["initContainers"], "init-config")
        self.assertNotIn("/kubarr-secrets/prowlarr", init["command"][-1])

    def test_only_init_config_reads_the_key_and_read_only(self):
        spec = pod(enabled(PROWLARR), "prowlarr")
        self.assertEqual(len(secret_volumes(spec)), 1)
        init = by_name(spec["initContainers"], "init-config")
        self.assertIs(mounts(init)["/kubarr-secrets/prowlarr"].get("readOnly"), True)
        app = by_name(spec["containers"], "prowlarr")
        self.assertNotIn("/kubarr-secrets/prowlarr", mounts(app))

    def run_init(self, existing_config):
        spec = pod(enabled(PROWLARR), "prowlarr")
        script = by_name(spec["initContainers"], "init-config")["command"][-1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "secret").mkdir()
            (tmp / "secret" / "api-key").write_text(KEY + "\n")
            (tmp / "config").mkdir()
            if existing_config is not None:
                (tmp / "config" / "config.xml").write_text(existing_config)
            script = (script.replace("/kubarr-secrets/prowlarr", str(tmp / "secret"))
                      .replace('"/config/config.xml"', f'"{tmp}/config/config.xml"')
                      .replace("mkdir -p /config", f"mkdir -p {tmp}/config"))
            self.assertNotIn(" /config", script)
            result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return (tmp / "config" / "config.xml").read_text(), result.stdout

    def test_fresh_config_gets_kubarrs_key(self):
        config, log = self.run_init(None)
        self.assertIn(f"<ApiKey>{KEY}</ApiKey>", config)
        self.assertNotIn(KEY, log)

    def test_existing_key_is_replaced_and_never_logged(self):
        old = "fedcba9876543210fedcba9876543210"
        config, log = self.run_init(
            f"<Config>\n  <ApiKey>{old}</ApiKey>\n  <Port>9696</Port>\n</Config>\n")
        self.assertIn(f"<ApiKey>{KEY}</ApiKey>", config)
        self.assertNotIn(old, config)
        self.assertEqual(config.count("<ApiKey>"), 1)
        self.assertNotIn(KEY, log)
        self.assertNotIn(old, log)

    def test_pod_restarts_when_the_link_changes(self):
        def annotations(resources):
            for doc in resources:
                if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "prowlarr":
                    return doc["spec"]["template"]["metadata"]["annotations"]
        self.assertNotEqual(annotations(render(PROWLARR)), annotations(enabled(PROWLARR)))


class ManagerRegistrationTests(unittest.TestCase):
    def test_key_mounts_only_with_a_key_from_kubarr(self):
        for chart in ARRS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                self.assertEqual(secret_volumes(pod(render(chart), name)), [])
                spec = pod(enabled(chart), name)
                self.assertEqual(len(secret_volumes(spec)), 1)
                app = mounts(by_name(spec["containers"], name))
                self.assertIs(app["/kubarr-secrets/prowlarr"].get("readOnly"), True)
                self.assertIn("/custom-services.d/setup-prowlarr", app)

    def test_script_registers_this_manager_at_its_own_service(self):
        for chart, (app, url) in ARRS.items():
            with self.subTest(chart=chart):
                script = setup_script(render(chart))
                self.assertIn(f'APP="{app}"', script)
                self.assertIn(f'SELF_URL="{url}"', script)
                self.assertIn("prowlarr.prowlarr.svc.cluster.local:9696/prowlarr", script)
                syntax = subprocess.run(["bash", "-n"], input=script,
                                        capture_output=True, text=True)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_script_stays_idle_without_a_key(self):
        script = setup_script(render("media-manager/sonarr"))
        guard = script.index('if [ ! -s "$KEY_FILE" ]')
        self.assertLess(guard, script.index("prowlarr_send POST"))
        self.assertIn("exec sleep infinity", script[guard:guard + 200])

    def test_reserved_paths_cover_the_new_mounts(self):
        for chart in ARRS:
            with self.subTest(chart=chart):
                values = yaml.safe_load((ROOT / chart / "values.yaml").read_text())
                reserved = values["kubarr"]["storage"]["reserved"]["main"]
                self.assertIn("/custom-services.d/setup-prowlarr", reserved)
                self.assertIn("/kubarr-secrets/prowlarr", reserved)

    def test_network_policies_allow_both_directions(self):
        prowlarr = yaml.safe_load((ROOT / PROWLARR / "values.yaml").read_text())["networkPolicy"]
        for chart in ARRS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                policy = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["networkPolicy"]
                self.assertIn("prowlarr", policy["egressTo"])
                self.assertIn("prowlarr", policy["ingressFrom"])
                self.assertIn(name, prowlarr["egressTo"])
                self.assertIn(name, prowlarr["ingressFrom"])


if __name__ == "__main__":
    unittest.main()
