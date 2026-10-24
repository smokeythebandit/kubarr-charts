"""Regression for managed-NFS admission and service readiness on Talos."""
import os
import subprocess
import unittest
import yaml
from chart_artifacts import ROOT, chart_input


class ManagedNfsTests(unittest.TestCase):
    def test_privileged_server_has_scoped_admission_and_port_readiness(self):
        output = subprocess.check_output([
            os.environ.get("HELM", "helm"), "template", "managed-nfs",
            str(chart_input(ROOT / "system/managed-nfs")),
        ], text=True)
        resources = [item for item in yaml.safe_load_all(output) if item]
        namespace = next(item for item in resources if item["kind"] == "Namespace")
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        self.assertEqual(namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "privileged")
        self.assertEqual(deployment["metadata"]["namespace"], namespace["metadata"]["name"])
        pod = deployment["spec"]["template"]["spec"]
        self.assertTrue(pod["hostNetwork"])
        server, = pod["containers"]
        self.assertTrue(server["securityContext"]["privileged"])
        self.assertEqual(server["readinessProbe"]["tcpSocket"], {"port": "nfs"})
        self.assertEqual(server["startupProbe"]["tcpSocket"], {"port": "nfs"})
        self.assertEqual(server["ports"][0]["containerPort"], 2049)

    def test_userspace_mode_runs_ganesha_with_persistent_recovery_state(self):
        output = subprocess.check_output([
            os.environ.get("HELM", "helm"), "template", "managed-nfs",
            str(chart_input(ROOT / "system/managed-nfs")), "--set", "nfs.implementation=ganesha",
        ], text=True)
        resources = [item for item in yaml.safe_load_all(output) if item]
        deployment = next(item for item in resources if item["kind"] == "Deployment")
        config = next(item for item in resources if item["kind"] == "ConfigMap")["data"]["ganesha.conf"]
        server, = deployment["spec"]["template"]["spec"]["containers"]
        self.assertIn("nfs-provisioner:v4.0.8", server["image"])
        self.assertIn("ganesha.nfsd -F", server["command"][-1])
        self.assertIn("Name = VFS", config)
        self.assertIn("Protocols = 4", config)
        self.assertIn("RecoveryBackend = fs", config)
        self.assertIn("Only_Numeric_Owners = true", config)
        self.assertIn({"name": "data", "mountPath": "/var/lib/nfs/ganesha", "subPath": ".kubarr-nfs-recovery"}, server["volumeMounts"])
