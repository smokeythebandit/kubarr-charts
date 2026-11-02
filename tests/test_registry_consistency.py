"""What a chart promises must exist in a registry, and a change must ship.

Rendering proves a chart is well formed. It does not prove the images it names
can be pulled, and it does not prove the revision on disk will ever reach the
registry. Both of those failed in production and neither was visible here:

  * The platform charts deployed `kubarr-*:0.3.10`, a tag that was never
    pushed -- the v0.3.10 release failed its acceptance gate, so its publish
    job was skipped. Every chart rendered perfectly while naming an image that
    did not exist. It surfaced only when the installer tried to pull it.

  * `publish.yml` skips a chart whose version is already in the registry. A
    chart changed without a version bump is therefore published nowhere, and
    the registry keeps serving the old content under the same version.

These checks talk to the network, so they are skipped unless asked for:
set KUBARR_REGISTRY_CHECKS=1 (CI does). They are the last gate before publish,
not a unit test.
"""

import json
import os
import subprocess
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from chart_artifacts import ROOT, credential_args

REGISTRY = "ghcr.io/smokeythebandit/kubarr-charts"
ENABLED = os.environ.get("KUBARR_REGISTRY_CHECKS") == "1"
TIMEOUT = 30


def charts():
    for path in sorted(ROOT.glob("*/*/Chart.yaml")):
        metadata = yaml.safe_load(path.read_text())
        if metadata.get("type") == "library":
            continue
        yield path.parent, metadata


def rendered(chart):
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", chart.name, str(chart),
         *credential_args(chart)],
        capture_output=True, text=True,
    )
    if result.returncode:
        return []
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def images_of(chart):
    found = set()
    for doc in rendered(chart):
        if doc.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet", "CronJob"):
            continue
        spec = (doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                if doc["kind"] == "CronJob" else doc["spec"]["template"]["spec"])
        for container in spec["containers"] + spec.get("initContainers", []):
            found.add(container["image"])
    return found


def manifest_exists(image, attempts=3):
    """True if the image resolves, False if the registry says it is not there.

    A transport failure is not an answer: it raises after a retry, so a flaky
    network fails loudly instead of quietly reporting a missing image -- which
    it did once during this work, accusing Immich of a tag that was fine.
    """
    # An image may be pinned by digest, with or without a tag in front of it:
    # repo:tag@sha256:... and repo@sha256:... both resolve by the digest.
    if "@" in image:
        repository, _, reference = image.partition("@")
        repository = repository.rpartition(":")[0] if ":" in repository.rpartition("/")[2] else repository
    else:
        repository, _, reference = image.rpartition(":")
    host, _, path = repository.partition("/")
    if "." not in host and ":" not in host:
        host, path = "registry-1.docker.io", repository
        if "/" not in path:
            path = f"library/{path}"
    elif host == "docker.io":
        host = "registry-1.docker.io"

    url = f"https://{host}/v2/{path}/manifests/{reference}"
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])

    def ask(token):
        request = urllib.request.Request(url, method="HEAD")
        request.add_header("Accept", accept)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status == 200

    def token_for(challenge):
        realm, service, scope = "", "", f"repository:{path}:pull"
        for field in challenge.removeprefix("Bearer ").split(","):
            key, _, value = field.strip().partition("=")
            value = value.strip('"')
            if key == "realm":
                realm = value
            elif key == "service":
                service = value
        if not realm:
            return ""
        query = f"scope={scope}" + (f"&service={service}" if service else "")
        with urllib.request.urlopen(f"{realm}?{query}", timeout=TIMEOUT) as response:
            return json.load(response).get("token", "")

    last = None
    for attempt in range(attempts):
        try:
            return ask("")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            if error.code in (401, 403):
                challenge = error.headers.get("www-authenticate", "")
                if "Bearer" not in challenge:
                    return False
                try:
                    token = token_for(challenge)
                except (urllib.error.URLError, OSError) as failure:
                    last = failure
                    continue
                if not token:
                    return False
                try:
                    return ask(token)
                except urllib.error.HTTPError as authed:
                    if authed.code in (401, 403, 404):
                        return False
                    last = authed
                except (urllib.error.URLError, OSError) as failure:
                    last = failure
            else:
                last = error
        except (urllib.error.URLError, OSError) as failure:
            last = failure
    raise AssertionError(f"could not reach the registry for {image}: {last}")


def published_chart(name, version):
    """The published chart's metadata, or None when that version is absent."""
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "show", "chart",
         f"oci://{REGISTRY}/{name}", "--version", version],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        return None
    # helm prints "Pulled:", "Digest:" and an underscore-in-tag warning before
    # the chart metadata, because the registry stores 1.2.3+4 as 1.2.3_4.
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("apiVersion:"):
            return yaml.safe_load("\n".join(lines[index:]))
    return None


@unittest.skipUnless(ENABLED, "set KUBARR_REGISTRY_CHECKS=1 to query the registries")
class RegistryConsistencyTests(unittest.TestCase):
    def test_every_image_a_chart_deploys_can_be_pulled(self):
        wanted = {}
        for chart, _ in charts():
            for image in images_of(chart):
                wanted.setdefault(image, []).append(chart.name)
        self.assertTrue(wanted, "no images collected")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = dict(zip(wanted, pool.map(manifest_exists, wanted)))
        missing = {image: wanted[image] for image, ok in results.items() if not ok}
        self.assertEqual(
            missing, {},
            "these charts deploy an image that cannot be pulled; a release whose "
            "publish job was skipped leaves exactly this behind")

    def test_a_changed_chart_carries_a_version_that_will_publish(self):
        # publish.yml skips a version already in the registry, so reusing one
        # means the change never ships.
        for chart, metadata in charts():
            name, version = metadata["name"], str(metadata["version"])
            with self.subTest(chart=name):
                remote = published_chart(name, version)
                if remote is None:
                    continue  # not published yet: this version will publish
                self.assertEqual(
                    str(remote.get("appVersion")), str(metadata.get("appVersion")),
                    f"{name} {version} is already published with a different "
                    "appVersion; bump the chart version or the change will not ship")
                self.assertEqual(
                    remote.get("description"), metadata.get("description"),
                    f"{name} {version} is already published with different metadata; "
                    "bump the chart version or the change will not ship")


if __name__ == "__main__":
    unittest.main()
