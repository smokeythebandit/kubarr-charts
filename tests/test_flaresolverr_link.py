"""Jackett and Prowlarr keep FlareSolverr in sync while it is installed.

Each chart ships a setup-flaresolverr service that points the indexer app at
the in-cluster FlareSolverr while it answers and removes the link once it is
gone. Setting flaresolverr.url to empty leaves the app alone.
"""

import os
import subprocess
import unittest
from pathlib import Path

import yaml

from chart_artifacts import ROOT, chart_input


CHARTS = ("indexer/jackett", "indexer/prowlarr")
URL = "http://flaresolverr.flaresolverr.svc.cluster.local:8191"
MOUNT = "/custom-services.d/setup-flaresolverr"


def render(chart, *sets):
    command = [os.environ.get("HELM", "helm"), "template", Path(chart).name,
               str(chart_input(ROOT / chart))]
    for value in sets:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{chart}: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def app_container(resources, name):
    for doc in resources:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == name:
            return next(c for c in doc["spec"]["template"]["spec"]["containers"]
                        if c["name"] == name)
    raise AssertionError(f"no Deployment {name}")


def script(resources):
    for doc in resources:
        if doc["kind"] == "ConfigMap" and "setup-flaresolverr.sh" in doc.get("data", {}):
            return doc["data"]["setup-flaresolverr.sh"]
    return None


class FlareSolverrSyncTests(unittest.TestCase):
    def test_sync_service_is_mounted_by_default(self):
        for chart in CHARTS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                resources = render(chart)
                mounts = {m["mountPath"] for m in app_container(resources, name)["volumeMounts"]}
                self.assertIn(MOUNT, mounts)
                body = script(resources)
                self.assertIn(URL, body)
                syntax = subprocess.run(["bash", "-n"], input=body,
                                        capture_output=True, text=True)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_empty_url_leaves_the_app_alone(self):
        for chart in CHARTS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                resources = render(chart, "flaresolverr.url=")
                self.assertIsNone(script(resources))
                mounts = {m["mountPath"] for m in app_container(resources, name)["volumeMounts"]}
                self.assertNotIn(MOUNT, mounts)

    def test_release_from_before_the_setting_still_syncs(self):
        # helm upgrade --reuse-values drops new chart defaults entirely.
        for chart in CHARTS:
            with self.subTest(chart=chart):
                self.assertIn(URL, script(render(chart, "flaresolverr=null")))

    def test_removal_waits_for_repeated_misses(self):
        for chart in CHARTS:
            with self.subTest(chart=chart):
                body = script(render(chart))
                self.assertIn("GONE_AFTER=3", body)
                self.assertIn('"$misses" -ge "$GONE_AFTER"', body)

    def test_only_the_links_this_service_made_are_removed(self):
        jackett = script(render("indexer/jackett"))
        self.assertIn('[ "$url" = "$FLARESOLVERR_URL" ]', jackett)
        prowlarr = script(render("indexer/prowlarr"))
        self.assertIn('id=$(ours "$proxies")', prowlarr)

    def test_jackett_calls_count_only_when_jackett_returns_its_config(self):
        body = script(render("indexer/jackett"))
        self.assertNotIn("curl -s -f -b", body)
        self.assertIn('has("port")', body)
        is_config = body[body.index("is_config() {"):]
        is_config = is_config[:is_config.index("}") + 1]
        # Reads answer in camelCase, saves echo the stored PascalCase file.
        for sample, ok in (('{"port":9117,"flaresolverrurl":""}', True),
                           ('{"Port":9117,"FlareSolverrUrl":""}', True),
                           ("", False), ("<html>login</html>", False)):
            result = subprocess.run(["bash", "-c", is_config + "\nis_config"],
                                    input=sample, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, sample)

    def test_jackett_with_an_admin_password_is_left_to_the_user(self):
        body = script(render("indexer/jackett"))
        guard = body.index("if ! login; then")
        self.assertIn("exec sleep infinity", body[guard:guard + 200])

    def test_reserved_paths_and_network_policy(self):
        flaresolverr = yaml.safe_load(
            (ROOT / "indexer/flaresolverr/values.yaml").read_text())["networkPolicy"]
        for chart in CHARTS:
            name = Path(chart).name
            with self.subTest(chart=chart):
                values = yaml.safe_load((ROOT / chart / "values.yaml").read_text())
                self.assertIn(MOUNT, values["kubarr"]["storage"]["reserved"]["main"])
                self.assertIn("flaresolverr", values["networkPolicy"]["egressTo"])
                self.assertIn(name, flaresolverr["ingressFrom"])


if __name__ == "__main__":
    unittest.main()
