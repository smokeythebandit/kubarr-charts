"""Jellyfin Enhanced: a pinned, checksum-verified plugin that never blocks Jellyfin."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import zipfile

import yaml

from chart_artifacts import ROOT, chart_input


JELLYFIN = "media-server/jellyfin"
DLL = "Jellyfin.Plugin.JellyfinEnhanced.dll"


def render(chart=JELLYFIN, *sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(chart).name,
               str(chart_input(ROOT / chart))]
    for value in sets:
        command += ["--set", value]
    return subprocess.run(command, capture_output=True, text=True)


def docs(chart=JELLYFIN, *sets):
    result = render(chart, *sets)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def init_containers(*sets):
    deployment = next(d for d in docs(JELLYFIN, *sets) if d["kind"] == "Deployment")
    return {c["name"]: c for c in deployment["spec"]["template"]["spec"].get("initContainers", [])}


def ingress_namespaces(chart):
    policy = next(d for d in docs(chart) if d["kind"] == "NetworkPolicy"
                  and d["metadata"]["name"] == Path(chart).name)
    return {peer["namespaceSelector"].get("matchLabels", {}).get("kubernetes.io/metadata.name")
            for rule in policy["spec"]["ingress"] for peer in rule["from"]
            if "namespaceSelector" in peer}


class RenderTests(unittest.TestCase):
    def test_installed_by_default_with_the_pinned_release(self):
        container = init_containers()["install-jellyfin-enhanced"]
        env = {e["name"]: e["value"] for e in container["env"]}
        self.assertRegex(env["VERSION"], r"^\d+(\.\d+){3}$")
        self.assertRegex(env["SHA256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            env["URL"],
            "https://github.com/n00bcodr/Jellyfin-Enhanced/releases/download/"
            f"{env['VERSION']}/Jellyfin.Plugin.JellyfinEnhanced_12.0.0.zip")
        context = container["securityContext"]
        self.assertTrue(context["readOnlyRootFilesystem"])
        self.assertFalse(context["allowPrivilegeEscalation"])
        self.assertEqual(context["capabilities"], {"drop": ["ALL"]})
        self.assertEqual(container["volumeMounts"], [{"name": "config", "mountPath": "/config"}])

    def test_disabled_leaves_it_out(self):
        self.assertNotIn("install-jellyfin-enhanced", init_containers("jellyfinEnhanced.enabled=false"))

    def test_rejects_an_unpinned_release(self):
        for setting in ("jellyfinEnhanced.sha256=", "jellyfinEnhanced.sha256=ABC",
                        "jellyfinEnhanced.version=latest"):
            with self.subTest(setting=setting):
                self.assertNotEqual(render(JELLYFIN, setting).returncode, 0)

    def test_jellyfin_and_the_apps_it_calls_allow_each_other(self):
        policy = next(d for d in docs() if d["kind"] == "NetworkPolicy"
                      and d["metadata"]["name"] == "jellyfin")
        egress = {peer["namespaceSelector"].get("matchLabels", {}).get("kubernetes.io/metadata.name")
                  for rule in policy["spec"]["egress"] for peer in rule["to"]
                  if "namespaceSelector" in peer}
        for app, chart in (("sonarr", "media-manager/sonarr"), ("radarr", "media-manager/radarr"),
                           ("jellyseerr", "media-manager/jellyseerr"),
                           ("dispatcharr", "media-server/dispatcharr")):
            with self.subTest(app=app):
                self.assertIn(app, egress)
                self.assertIn("jellyfin", ingress_namespaces(chart))


    def test_kubarr_worker_may_configure_the_plugin_only_when_linked(self):
        def worker_policies(*sets):
            return [d for d in docs(JELLYFIN, *sets) if d["kind"] == "NetworkPolicy"
                    and d["metadata"]["name"].endswith("-kubarr-worker")]
        self.assertEqual(worker_policies(), [])
        linked = "jellyfinIntegration.secretName=kubarr-jellyfin-api-key"
        policy, = worker_policies(linked)
        rule, = policy["spec"]["ingress"]
        self.assertEqual(rule["from"], [{"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": "kubarr-worker"}}}])
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 8096}])
        # Live TV setup needs it too, so it no longer depends on Jellyfin Enhanced.
        self.assertEqual(len(worker_policies(linked, "jellyfinEnhanced.enabled=false")), 1)


    def test_kubarr_worker_may_exec_only_in_each_apps_own_namespace(self):
        for app, chart in (("sonarr", "media-manager/sonarr"), ("radarr", "media-manager/radarr"),
                           ("jellyseerr", "media-manager/jellyseerr")):
            with self.subTest(app=app):
                rendered = docs(chart)
                role, = [d for d in rendered if d["kind"] == "Role"
                         and d["metadata"]["name"].endswith("-kubarr-config-access")]
                self.assertEqual(role["metadata"]["namespace"], app)
                self.assertEqual(role["rules"], [{"apiGroups": [""], "resources": ["pods/exec"],
                                                  "verbs": ["get", "create"]}])
                binding, = [d for d in rendered if d["kind"] == "RoleBinding"
                            and d["metadata"]["name"].endswith("-kubarr-config-access")]
                self.assertEqual(binding["metadata"]["namespace"], app)
                self.assertEqual(binding["subjects"], [{"kind": "ServiceAccount", "name": "kubarr",
                                                        "namespace": "kubarr-worker"}])
                self.assertFalse(any(d["kind"] in ("ClusterRole", "ClusterRoleBinding") for d in rendered))
                off = docs(chart, "kubarrConfigAccess.enabled=false")
                self.assertFalse([d for d in off if d["kind"] in ("Role", "RoleBinding")
                                  and d["metadata"]["name"].endswith("-kubarr-config-access")])


class InstallScriptTests(unittest.TestCase):
    """Run the rendered script with a stub wget serving a local archive."""

    def setUp(self):
        container = init_containers()["install-jellyfin-enhanced"]
        self.script = container["command"][2]
        self.env = {e["name"]: e["value"] for e in container["env"]}
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.plugins = root / "plugins"
        self.archive = root / "release.zip"
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(DLL, b"not really a dll")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        wget = bin_dir / "wget"
        # Arguments: -q -T 60 -O <file> <url>; exit 1 when FAIL_DOWNLOAD is set.
        wget.write_text('#!/bin/sh\n[ -z "${FAIL_DOWNLOAD:-}" ] || exit 1\n'
                        f'cp "{self.archive}" "$5"\n')
        wget.chmod(wget.stat().st_mode | stat.S_IEXEC)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, sha256=None, **extra):
        env = {**self.env, "PATH": self.path, "PLUGIN_DIR": str(self.plugins),
               "SHA256": sha256 or self.digest, **extra}
        return subprocess.run(["sh", "-c", self.script], env=env, capture_output=True, text=True)

    def installed(self):
        return self.plugins / f"Jellyfin Enhanced_{self.env['VERSION']}"

    def test_installs_the_release_with_metadata(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.installed() / DLL).is_file())
        meta = json.loads((self.installed() / "meta.json").read_text())
        self.assertEqual(meta["guid"], "f69e946a-4b3c-4e9a-8f0a-8d7c1b2c4d9b")
        self.assertEqual(meta["version"], self.env["VERSION"])
        self.assertEqual(meta["targetAbi"], self.env["TARGET_ABI"])
        self.assertFalse(meta["autoUpdate"])

    def test_a_second_start_does_not_download_again(self):
        self.run_script()
        result = self.run_script(FAIL_DOWNLOAD="1")
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.installed() / DLL).is_file())

    def test_a_checksum_mismatch_installs_nothing_but_does_not_block(self):
        result = self.run_script(sha256="0" * 64)
        self.assertEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stdout)
        self.assertFalse(self.installed().exists())
        self.assertEqual([p.name for p in self.plugins.iterdir()], [])

    def test_a_failed_download_does_not_block(self):
        result = self.run_script(FAIL_DOWNLOAD="1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("download failed", result.stdout)
        self.assertFalse(self.installed().exists())

    def test_other_versions_are_removed_so_one_copy_loads(self):
        old = self.plugins / "Jellyfin Enhanced_1.0.0.0"
        old.mkdir(parents=True)
        (self.plugins / "Kubarr_1.0.0.0").mkdir()
        self.run_script()
        names = sorted(p.name for p in self.plugins.iterdir())
        self.assertEqual(names, [self.installed().name, "Kubarr_1.0.0.0"])

    def test_keeps_jellyfins_own_meta_edits(self):
        self.run_script()
        meta = self.installed() / "meta.json"
        meta.write_text('{"status": "Disabled"}')
        self.run_script()
        self.assertEqual(meta.read_text(), '{"status": "Disabled"}')


if __name__ == "__main__":
    unittest.main()
