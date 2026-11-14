"""Lidarr searches and downloads through slskd with a key Kubarr shares.

Kubarr puts one API key in a Secret in the slskd and lidarr namespaces and
passes its name as slskdIntegration.secretName. slskd takes it as its API key;
Lidarr installs the Tubifarry plugin if it is missing, and its setup-slskd
service adds Tubifarry's Slskd indexer and download client with that key.
"""

import os
import subprocess
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


SECRET = "kubarr-slskd-api-key"
LIDARR = "media-manager/lidarr"
SLSKD = "download-client/slskd"


def render(chart, *sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(chart).name,
               str(chart_input(ROOT / chart))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def linked(chart, *sets):
    return render(chart, f"slskdIntegration.secretName={SECRET}", *sets)


def pod(resources):
    for doc in resources:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] in ("lidarr", "slskd"):
            return doc["spec"]["template"]["spec"]
    raise AssertionError("no Deployment")


def container(spec, name):
    return next(c for c in spec["containers"] if c["name"] == name)


def init_names(spec):
    return [c["name"] for c in spec.get("initContainers", [])]


def mounts(c):
    return {m["mountPath"]: m for m in c.get("volumeMounts", [])}


def api_key_ref(c):
    env = next(e for e in c.get("env", []) if e["name"] == "SLSKD_API_KEY")
    return env["valueFrom"]["secretKeyRef"]


def setup_script(resources):
    for doc in resources:
        if doc["kind"] == "ConfigMap" and "setup-slskd.sh" in doc.get("data", {}):
            return doc["data"]["setup-slskd.sh"]
    raise AssertionError("no setup-slskd.sh")


class LidarrSideTests(unittest.TestCase):
    def test_nothing_happens_without_a_key_from_kubarr(self):
        spec = pod(render(LIDARR))
        self.assertNotIn("install-tubifarry", init_names(spec))
        lidarr = mounts(container(spec, "lidarr"))
        self.assertNotIn("/custom-services.d/setup-slskd", lidarr)
        self.assertNotIn("/kubarr-secrets/slskd", lidarr)

    def test_key_is_mounted_read_only_with_the_setup_service(self):
        spec = pod(linked(LIDARR))
        lidarr = mounts(container(spec, "lidarr"))
        self.assertTrue(lidarr["/kubarr-secrets/slskd"]["readOnly"])
        self.assertEqual(lidarr["/custom-services.d/setup-slskd"]["subPath"], "setup-slskd.sh")
        volume = next(v for v in spec["volumes"] if v["name"] == "slskd-api-key")
        self.assertEqual(volume["secret"]["secretName"], SECRET)
        self.assertEqual(volume["secret"]["items"], [{"key": "api-key", "path": "api-key"}])

    def test_tubifarry_is_installed_pinned_and_checked(self):
        spec = pod(linked(LIDARR))
        init = next(c for c in spec["initContainers"] if c["name"] == "install-tubifarry")
        env = {e["name"]: e["value"] for e in init["env"]}
        self.assertRegex(env["SHA256"], r"^[0-9a-f]{64}$")
        self.assertIn(f"/v{env['VERSION']}/Tubifarry-v{env['VERSION']}.net8.0.zip", env["URL"])
        script = init["command"][-1]
        self.assertIn("sha256sum -c", script)
        # Lidarr updates the plugin itself; an installed copy is never replaced.
        self.assertIn('if [ -f "$dir/Lidarr.Plugin.Tubifarry.dll" ]', script)
        self.assertFalse(init["securityContext"]["allowPrivilegeEscalation"])
        self.assertIn("/config", mounts(init))

    def test_tubifarry_can_be_switched_off(self):
        spec = pod(linked(LIDARR, "tubifarry.enabled=false"))
        self.assertNotIn("install-tubifarry", init_names(spec))

    def test_bad_pin_fails_the_render(self):
        with self.assertRaises(AssertionError):
            linked(LIDARR, "tubifarry.sha256=nope")

    def test_setup_adds_indexer_and_client_with_the_shared_key(self):
        script = setup_script(linked(LIDARR))
        self.assertIn('SLSKD_URL="http://slskd.slskd.svc.cluster.local:5030/slskd"', script)
        self.assertIn('KEY_FILE="/kubarr-secrets/slskd/api-key"', script)
        self.assertIn("ensure downloadclient SlskdClient", script)
        self.assertIn("ensure indexer SlskdIndexer", script)
        # The key reaches Lidarr only in the request body, never a URL.
        self.assertNotIn("apikey=$SLSKD_KEY", script)

    def test_setup_script_is_valid_bash(self):
        script = setup_script(linked(LIDARR))
        result = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class SlskdSideTests(unittest.TestCase):
    VPN = ("vpn.enabled=true", "vpn.secretName=vpn-slskd", "vpn.portForwarding.enabled=true")

    def test_generated_key_is_used_without_the_link(self):
        spec = pod(render(SLSKD, *self.VPN))
        for name in ("slskd", "gluetun"):
            self.assertEqual(api_key_ref(container(spec, name)),
                             {"name": "slskd-credentials", "key": "api-key"})

    def test_shared_key_replaces_it_in_slskd_and_the_port_callback(self):
        spec = pod(linked(SLSKD, *self.VPN))
        for name in ("slskd", "gluetun"):
            self.assertEqual(api_key_ref(container(spec, name)),
                             {"name": SECRET, "key": "api-key"})

    def test_soulseek_account_still_comes_from_the_generated_secret(self):
        slskd = container(pod(linked(SLSKD)), "slskd")
        env = {e["name"]: e for e in slskd["env"]}
        self.assertEqual(env["SLSKD_SLSK_USERNAME"]["valueFrom"]["secretKeyRef"]["name"],
                         "slskd-credentials")


if __name__ == "__main__":
    unittest.main()
