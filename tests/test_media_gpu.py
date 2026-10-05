"""GPU opt-in render coverage for the two media servers."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHARTS = {"plex": "media-server/plex", "jellyfin": "media-server/jellyfin"}


def render(app, overrides, set_flags=()):
    with tempfile.TemporaryDirectory() as directory:
        values = Path(directory) / "values.yaml"
        values.write_text(yaml.safe_dump(overrides))
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "media-gpu-test",
             str(chart_input(ROOT / CHARTS[app])), "-f", str(values), *set_flags],
            capture_output=True, text=True,
        )
    if result.returncode:
        return result, None
    deployment = next(doc for doc in yaml.safe_load_all(result.stdout)
                      if doc and doc["kind"] == "Deployment")
    return result, deployment


class MediaGpuTests(unittest.TestCase):
    def test_legacy_release_without_gpu_values(self):
        for app in CHARTS:
            # A pre-GPU release has no gpu key in its stored values. -f models
            # those retained values; --set gpu=null also exercises Helm's
            # null override, which removes the new chart's gpu defaults.
            legacy = {
                "podSecurityContext": {"fsGroup": 1000},
                app: {"resources": {"requests": {"cpu": "500m", "memory": "1Gi"},
                                    "limits": {"cpu": "2", "memory": "3Gi"}}},
            }
            for flags in ((), ("--set", "gpu=null")):
                with self.subTest(app=app, flags=flags):
                    result, deployment = render(app, legacy, flags)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("strategy", deployment["spec"])
                    pod = deployment["spec"]["template"]["spec"]
                    self.assertNotIn("runtimeClassName", pod)
                    self.assertEqual(pod["securityContext"]["fsGroup"], 1000)
                    self.assertNotIn("supplementalGroups", pod["securityContext"])
                    media = next(c for c in pod["containers"] if c["name"] == app)
                    self.assertNotIn("env", media)
                    self.assertEqual(media["resources"]["requests"],
                                     legacy[app]["resources"]["requests"])
                    self.assertEqual(media["resources"]["limits"],
                                     legacy[app]["resources"]["limits"])

    def test_reuse_values_cleanup_drops_null_resources_and_node_selector(self):
        resource_names = ("gpu.intel.com/i915", "gpu.intel.com/xe",
                          "nvidia.com/gpu", "nvidia.com/gpu.shared",
                          "example.com/shared-drm")
        for app in CHARTS:
            # Simulate old release values retained by --reuse-values. Helm --set
            # null merges into them rather than deleting the old map entries.
            old_values = {
                "gpu": {"enabled": True, "provider": "intel", "resourceName": "gpu.intel.com/i915"},
                "nodeSelector": {"kubernetes.io/hostname": "old-node"},
                app: {"resources": {
                    "requests": {"cpu": "500m", "memory": "1Gi", "gpu.intel.com/i915": 1},
                    "limits": {"cpu": "2", "memory": "3Gi", "gpu.intel.com/i915": 1},
                }},
            }
            cleanup = ["--set", "nodeSelector.kubernetes\\.io/hostname=null"]
            for side in ("requests", "limits"):
                for name in resource_names:
                    escaped_name = name.replace(".", "\\.")
                    cleanup.extend(("--set", f"{app}.resources.{side}.{escaped_name}=null"))

            for enabled in (False, True):
                with self.subTest(app=app, enabled=enabled):
                    flags = [*cleanup, "--set", f"gpu.enabled={str(enabled).lower()}"]
                    if enabled:
                        flags += ["--set", "gpu.provider=nvidia", "--set",
                                  "gpu.resourceName=nvidia.com/gpu.shared"]
                    result, deployment = render(app, old_values, flags)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    pod = deployment["spec"]["template"]["spec"]
                    self.assertNotIn("nodeSelector", pod)
                    media = next(c for c in pod["containers"] if c["name"] == app)
                    for side, expected in (("requests", {"cpu": "500m", "memory": "1Gi"}),
                                           ("limits", {"cpu": "2", "memory": "3Gi"})):
                        if enabled:
                            expected = {**expected, "nvidia.com/gpu.shared": 1}
                        self.assertEqual(media["resources"][side], expected)

    def test_cleanup_preserves_other_node_selectors(self):
        for app in CHARTS:
            with self.subTest(app=app):
                result, deployment = render(
                    app, {"nodeSelector": {"kubernetes.io/hostname": "old-node",
                                          "kubernetes.io/arch": "amd64"}},
                    ["--set", "nodeSelector.kubernetes\\.io/hostname=null"],
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                pod = deployment["spec"]["template"]["spec"]
                self.assertEqual(pod["nodeSelector"], {"kubernetes.io/arch": "amd64"})

    def test_defaults_do_not_allocate_devices_or_change_rollout(self):
        for app in CHARTS:
            with self.subTest(app=app):
                result, deployment = render(app, {})
                self.assertEqual(result.returncode, 0, result.stderr)
                pod = deployment["spec"]["template"]["spec"]
                container = next(c for c in pod["containers"] if c["name"] == app)
                self.assertNotIn("strategy", deployment["spec"])
                self.assertNotIn("runtimeClassName", pod)
                self.assertNotIn("supplementalGroups", pod["securityContext"])
                self.assertNotIn("env", container)
                self.assertEqual(container["resources"]["requests"]["cpu"], "200m")
                self.assertEqual(container["resources"]["limits"]["memory"], "4Gi")
                self.assertFalse(any("gpu" in key for side in container["resources"].values()
                                     for key in side))
                self.assertFalse(any("hostPath" in volume for volume in pod["volumes"]))

    def test_providers_with_and_without_vpn(self):
        cases = (("intel", "", "gpu.intel.com/i915"),
                 ("intel", "gpu.intel.com/xe", "gpu.intel.com/xe"),
                 ("nvidia", "", "nvidia.com/gpu"),
                 ("nvidia", "nvidia.com/gpu.shared", "nvidia.com/gpu.shared"),
                 ("amd", "example.com/shared-drm", "example.com/shared-drm"))
        for app in CHARTS:
            for provider, configured, resource in cases:
                for vpn in (False, True):
                    with self.subTest(app=app, provider=provider, resource=resource, vpn=vpn):
                        overrides = {
                            "gpu": {"enabled": True, "provider": provider,
                                    "resourceName": configured, "runtimeClassName": "gpu-runtime",
                                    "supplementalGroups": [44, 109]},
                            "vpn": {"enabled": vpn, "secretName": "vpn-credentials"},
                            "podSecurityContext": {"fsGroup": 1000, "supplementalGroups": [44]},
                            app: {"resources": {"requests": {"cpu": "500m", "memory": "1Gi"},
                                                "limits": {"cpu": "2", "memory": "3Gi"}}},
                        }
                        result, deployment = render(app, overrides)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
                        pod = deployment["spec"]["template"]["spec"]
                        self.assertEqual(pod["runtimeClassName"], "gpu-runtime")
                        self.assertEqual(pod["securityContext"]["supplementalGroups"], [44, 109])
                        self.assertEqual(pod["securityContext"]["fsGroup"], 1000)
                        containers = {c["name"]: c for c in pod["containers"]}
                        self.assertEqual(set(containers), {app, "gluetun"} if vpn else {app})
                        media = containers[app]
                        for side in ("requests", "limits"):
                            self.assertEqual(media["resources"][side][resource], 1)
                        self.assertEqual(media["resources"]["requests"]["cpu"], "500m")
                        self.assertEqual(media["resources"]["limits"]["memory"], "3Gi")
                        self.assertEqual("NVIDIA_DRIVER_CAPABILITIES" in
                                         {e["name"] for e in media.get("env", [])}, provider == "nvidia")
                        if provider == "nvidia":
                            self.assertEqual(media["env"], [{"name": "NVIDIA_DRIVER_CAPABILITIES",
                                                             "value": "video,utility"}])
                        host_paths = [v["hostPath"]["path"] for v in pod["volumes"]
                                      if "hostPath" in v]
                        self.assertEqual(host_paths, ["/dev/net/tun"] if vpn else [])
                        if vpn:
                            self.assertNotIn(resource, containers["gluetun"]["resources"]["limits"])

    def test_optional_settings_and_invalid_amd(self):
        for app in CHARTS:
            with self.subTest(app=app):
                result, deployment = render(app, {"gpu": {"enabled": True}})
                self.assertEqual(result.returncode, 0, result.stderr)
                pod = deployment["spec"]["template"]["spec"]
                self.assertNotIn("runtimeClassName", pod)
                self.assertNotIn("supplementalGroups", pod["securityContext"])
                result, _ = render(app, {"gpu": {"enabled": True, "provider": "amd"}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("gpu.resourceName is required for AMD", result.stderr)


if __name__ == "__main__":
    unittest.main()
