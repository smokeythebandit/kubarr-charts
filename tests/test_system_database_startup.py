"""The system database must start on a root-squashed NFS export."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "system/postgresql"


def render(*values):
    command = [os.environ.get("HELM", "helm"), "template", CHART.name, str(chart_input(CHART))]
    for value in values:
        command.extend(["--set", value])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def statefulset(resources):
    return next(resource for resource in resources if resource["kind"] == "StatefulSet")


class SystemDatabaseStartupTests(unittest.TestCase):
    def pod(self, *values):
        return statefulset(render(*values))["spec"]["template"]["spec"]

    def test_data_subpath_is_prepared_without_relying_on_squashed_root(self):
        # Kubelet creates subPath directories as root. A normal export squashes
        # root, so the database never starts unless the chart prepares its own
        # directory as the media group.
        pod = self.pod()
        prepare, = pod["initContainers"]
        self.assertEqual(prepare["name"], "prepare-media-directories")
        self.assertEqual(prepare["args"], ["/nfs/system/postgresql"])
        self.assertEqual(prepare["volumeMounts"], [{"name": "media", "mountPath": "/nfs"}])
        context = prepare["securityContext"]
        self.assertEqual(context["runAsUser"], 999)
        self.assertEqual(context["runAsGroup"], 1000)
        self.assertTrue(context["runAsNonRoot"])
        self.assertFalse(context["allowPrivilegeEscalation"])
        self.assertEqual(context["capabilities"], {"drop": ["ALL"]})
        self.assertIn(1000, pod["securityContext"]["supplementalGroups"])

    def test_preparation_is_idempotent_and_leaves_the_media_group_writable(self):
        prepare, = self.pod()["initContainers"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "system" / "postgresql"
            for _ in range(2):
                subprocess.run([*prepare["command"], str(target)],
                               check=True, capture_output=True, text=True)
            self.assertTrue(target.is_dir())
            self.assertEqual(target.stat().st_mode & 0o777, 0o775)

    def test_chart_refuses_a_data_mount_it_could_not_prepare(self):
        # Preparation only covers writable mounts, and the database contract
        # already rejects anything else, so no unprepared mount can reach a Pod.
        with self.assertRaises(subprocess.CalledProcessError):
            render("storage.mounts[0].mode=read")


if __name__ == "__main__":
    unittest.main()
