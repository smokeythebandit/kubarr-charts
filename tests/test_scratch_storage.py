"""Node-local scratch space for transcoders, kept off the shared export.

A transcoder writes HLS segments at the full output bitrate and regenerates
anything it loses, so the shared NFS export is the wrong home for them: a
single 4K session can write over 100 GB, and every segment crosses the link
three times (written, read back, delivered). storage.scratch moves that to the
node, and insists on a size cap so an unbounded transcode evicts the pod
instead of filling the node and taking its neighbours down with it.
"""

from pathlib import Path
import unittest

import yaml

from chart_artifacts import ROOT
from test_declarative_storage import pods, render


SCRATCH_CHARTS = {"media-server/jellyfin": "/cache", "media-server/plex": "/transcode",
                  "media-server/tdarr": "/temp"}


def container_of(resources, name):
    pod = next(pod for _, pod in pods(resources)
               if any(container["name"] == name for container in pod["containers"]))
    return pod, next(c for c in pod["containers"] if c["name"] == name)


def defaults(chart):
    return yaml.safe_load((ROOT / chart / "values.yaml").read_text())


# Helm coalesces values maps, so a chart default survives an override that
# simply omits the key. Dropping one takes an explicit null.
def nfs(chart, target):
    return {"backend": "nfs", "target": target, "sizeLimit": None,
            "source": f"scratch/{Path(chart).name}"}


class ScratchStorageTests(unittest.TestCase):
    def test_transcode_scratch_is_node_local_not_the_shared_claim(self):
        for chart, target in SCRATCH_CHARTS.items():
            with self.subTest(chart=chart):
                result, resources = render(chart)
                self.assertEqual(result.returncode, 0, result.stderr)
                pod, container = container_of(resources, Path(chart).name)
                mount = next(m for m in container["volumeMounts"] if m["mountPath"] == target)
                volume = next(v for v in pod["volumes"] if v["name"] == mount["name"])
                self.assertIn("emptyDir", volume, f"{target} must not be shared storage")
                self.assertNotIn("persistentVolumeClaim", volume)
                self.assertNotIn("subPath", mount)

    def test_node_local_scratch_is_always_capped(self):
        for chart, target in SCRATCH_CHARTS.items():
            with self.subTest(chart=chart):
                _, resources = render(chart)
                pod, container = container_of(resources, Path(chart).name)
                mount = next(m for m in container["volumeMounts"] if m["mountPath"] == target)
                volume = next(v for v in pod["volumes"] if v["name"] == mount["name"])
                self.assertTrue(volume["emptyDir"].get("sizeLimit"),
                                "an uncapped emptyDir can fill the node and evict neighbours")

    def test_uncapped_node_scratch_is_rejected(self):
        for chart in SCRATCH_CHARTS:
            with self.subTest(chart=chart):
                scratch = {**defaults(chart)["storage"]["scratch"], "sizeLimit": None}
                result, _ = render(chart, {"storage": {"scratch": scratch}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("sizeLimit", result.stderr)

    def test_scratch_rejects_unknown_keys_and_backends(self):
        for chart in SCRATCH_CHARTS:
            base = defaults(chart)["storage"]["scratch"]
            for override, expected in (
                ({**base, "backend": "nfs-subdir"}, "backend"),
                ({**base, "typo": "x"}, "not supported"),
                ({**base, "target": "/cache/../etc"}, "target"),
                ({**base, "sizeLimit": "0Gi"}, "sizeLimit"),
                ({**base, "sizeLimit": "24GB"}, "sizeLimit"),
            ):
                with self.subTest(chart=chart, override=override):
                    result, _ = render(chart, {"storage": {"scratch": override}})
                    self.assertNotEqual(result.returncode, 0, override)
                    self.assertIn(expected, result.stderr)

    def test_a_shared_mount_cannot_overlap_the_scratch_target(self):
        for chart, target in SCRATCH_CHARTS.items():
            with self.subTest(chart=chart):
                mounts = defaults(chart)["storage"]["mounts"]
                clash = [*mounts, {"name": "clash", "source": "cache/clash",
                                   "target": target, "mode": "read-write"}]
                result, _ = render(chart, {"storage": {"mounts": clash}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("overlaps", result.stderr)

    def test_nfs_backend_still_available_for_a_small_node(self):
        """A node too small for the cache can put it back on the export."""
        for chart, target in SCRATCH_CHARTS.items():
            with self.subTest(chart=chart):
                result, resources = render(
                    chart, {"storage": {"scratch": nfs(chart, target)}})
                self.assertEqual(result.returncode, 0, result.stderr)
                pod, container = container_of(resources, Path(chart).name)
                mount = next(m for m in container["volumeMounts"] if m["mountPath"] == target)
                self.assertEqual(mount["name"], "media")
                self.assertEqual(mount["subPath"], f"scratch/{Path(chart).name}")
                self.assertFalse([v for v in pod["volumes"] if v["name"] == "scratch"])

    def test_nfs_backend_prepares_its_directory_as_the_application_uid(self):
        """Kubelet creates subPath directories as root, which root_squash blocks."""
        for chart, target in SCRATCH_CHARTS.items():
            with self.subTest(chart=chart):
                result, resources = render(
                    chart, {"storage": {"scratch": nfs(chart, target)}})
                self.assertEqual(result.returncode, 0, result.stderr)
                pod, _ = container_of(resources, Path(chart).name)
                prepare = next(c for c in pod["initContainers"]
                               if c["name"] == "prepare-media-directories")
                self.assertIn(f"/nfs/scratch/{Path(chart).name}", prepare["args"])

    def test_nfs_and_node_fields_do_not_leak_across_backends(self):
        for chart, target in SCRATCH_CHARTS.items():
            for override, expected in (
                ({"backend": "node", "target": target, "sizeLimit": "8Gi",
                  "source": "scratch/x"}, "source"),
                ({"backend": "nfs", "target": target, "source": "scratch/x",
                  "sizeLimit": "8Gi"}, "sizeLimit"),
                ({"backend": "nfs", "target": target, "sizeLimit": None}, "source"),
            ):
                with self.subTest(chart=chart, override=override):
                    result, _ = render(chart, {"storage": {"scratch": override}})
                    self.assertNotEqual(result.returncode, 0, override)
                    self.assertIn(expected, result.stderr)

    def test_dropping_scratch_entirely_frees_its_path_for_a_shared_mount(self):
        """The full escape hatch back to the pre-scratch layout."""
        for chart, target in SCRATCH_CHARTS.items():
            with self.subTest(chart=chart):
                mounts = [*defaults(chart)["storage"]["mounts"],
                          {"name": "scratch", "source": f"scratch/{Path(chart).name}",
                           "target": target, "mode": "read-write"}]
                result, resources = render(
                    chart, {"storage": {"scratch": None, "mounts": mounts}})
                self.assertEqual(result.returncode, 0, result.stderr)
                pod, container = container_of(resources, Path(chart).name)
                mount = next(m for m in container["volumeMounts"] if m["mountPath"] == target)
                self.assertEqual(mount["name"], "media")
                self.assertFalse([v for v in pod["volumes"] if v["name"] == "scratch"])

    def test_charts_without_scratch_gain_no_extra_volume(self):
        for chart in ("media-manager/sonarr", "download-client/qbittorrent"):
            with self.subTest(chart=chart):
                result, resources = render(chart)
                self.assertEqual(result.returncode, 0, result.stderr)
                for _, pod in pods(resources):
                    self.assertFalse([v for v in pod["volumes"] if v["name"] == "scratch"])


if __name__ == "__main__":
    unittest.main()
