"""Jellyfin Enhanced's optional companion plugins are installed pinned.

An init container downloads each enabled plugin from jellyfinPlugins, checks
its sha256 and unpacks it into Jellyfin's plugin folder before Jellyfin starts.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


CHART = "media-server/jellyfin"
NAMES = ["File Transformation", "Plugin Pages", "Custom Tabs", "Intro Skipper",
         "InPlayerEpisodePreview"]


def render(*sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(CHART).name,
               str(chart_input(ROOT / CHART))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def with_plugin(index, **changes):
    """Render with one plugin changed; --set would replace the whole list."""
    values = yaml.safe_load((ROOT / CHART / "values.yaml").read_text())
    plugins = values["jellyfinPlugins"]["plugins"]
    plugins[index].update(changes)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump({"jellyfinPlugins": {"plugins": plugins}}, f)
        f.flush()
        command = [os.environ.get("HELM", "helm"), "template", Path(CHART).name,
                   str(chart_input(ROOT / CHART)), "-f", f.name]
        result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def installer(resources):
    for doc in resources:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "jellyfin":
            for c in doc["spec"]["template"]["spec"].get("initContainers", []):
                if c["name"] == "install-jellyfin-plugins":
                    return c
            return None
    raise AssertionError("no jellyfin Deployment")


class JellyfinPluginTests(unittest.TestCase):
    def test_every_companion_is_installed_by_default(self):
        script = installer(render())["command"][-1]
        for name in NAMES:
            self.assertIn(f"install '{name}' ", script)
        self.assertIn("sha256sum -c", script)

    def test_values_pin_each_release(self):
        values = yaml.safe_load((ROOT / CHART / "values.yaml").read_text())
        for plugin in values["jellyfinPlugins"]["plugins"]:
            self.assertRegex(plugin["sha256"], r"^[0-9a-f]{64}$", plugin["name"])
            self.assertTrue(plugin["url"].startswith("https://github.com/"), plugin["name"])
            self.assertIn(plugin["version"].rsplit(".", 1)[0].split(".")[0], plugin["url"])

    def test_one_plugin_can_be_switched_off(self):
        script = installer(with_plugin(3, enabled=False))["command"][-1]
        self.assertNotIn("install 'Intro Skipper'", script)
        self.assertIn("install 'Custom Tabs'", script)

    def test_all_can_be_switched_off(self):
        self.assertIsNone(installer(render("jellyfinPlugins.enabled=false")))

    def test_bad_checksum_fails_the_render(self):
        with self.assertRaises(AssertionError):
            with_plugin(0, sha256="nope")

    def test_installer_is_unprivileged_and_valid_shell(self):
        c = installer(render())
        self.assertTrue(c["securityContext"]["runAsNonRoot"])
        self.assertFalse(c["securityContext"]["allowPrivilegeEscalation"])
        result = subprocess.run(["sh", "-n"], input=c["command"][-1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
