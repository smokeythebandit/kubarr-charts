"""The system database's dumps must land on a root-squashed NFS export.

The database itself now lives on node-local volumes owned by CloudNativePG; the
shared export only receives the nightly dump. That job still has to create its
directory without root, which a normal export squashes.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input, credential_args


CHART = ROOT / "system/postgresql"


def render(*values):
    command = [os.environ.get("HELM", "helm"), "template", CHART.name, str(chart_input(CHART))]
    command.extend(credential_args(CHART))
    for value in values:
        command.extend(["--set", value])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def backup_pod(resources):
    cronjob = next(resource for resource in resources if resource["kind"] == "CronJob")
    return cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]


class SystemDatabaseBackupStorageTests(unittest.TestCase):
    def pod(self, *values):
        return backup_pod(render(*values))

    def test_dump_subpath_is_prepared_without_relying_on_squashed_root(self):
        # Kubelet creates subPath directories as root. A normal export squashes
        # root, so the dump never lands unless the chart prepares its own
        # directory as the media group.
        pod = self.pod()
        prepare, = pod["initContainers"]
        self.assertEqual(prepare["name"], "prepare-media-directories")
        self.assertEqual(prepare["args"], ["/nfs/system/postgresql-backups"])
        self.assertEqual(prepare["volumeMounts"], [{"name": "media", "mountPath": "/nfs"}])
        context = prepare["securityContext"]
        self.assertEqual(context["runAsUser"], 1000)
        self.assertEqual(context["runAsGroup"], 1000)
        self.assertTrue(context["runAsNonRoot"])
        self.assertFalse(context["allowPrivilegeEscalation"])
        self.assertEqual(context["capabilities"], {"drop": ["ALL"]})
        self.assertIn(1000, pod["securityContext"]["supplementalGroups"])

    def test_preparation_is_idempotent_and_leaves_the_media_group_writable(self):
        prepare, = self.pod()["initContainers"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "system" / "postgresql-backups"
            for _ in range(2):
                subprocess.run([*prepare["command"], str(target)],
                               check=True, capture_output=True, text=True)
            self.assertTrue(target.is_dir())
            self.assertEqual(target.stat().st_mode & 0o777, 0o775)

    def test_chart_refuses_a_dump_mount_it_could_not_prepare(self):
        # Preparation only covers writable mounts, and the backup contract
        # already rejects anything else, so no unprepared mount can reach a Pod.
        with self.assertRaises(subprocess.CalledProcessError):
            render("storage.mounts[0].mode=read")


if __name__ == "__main__":
    unittest.main()
