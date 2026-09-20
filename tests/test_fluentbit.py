"""Verify Fluent Bit keeps its positions database node-local and starts safely."""

import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "monitoring/fluent-bit"


def render(*values):
    command = [
        os.environ.get("HELM", "helm"),
        "template",
        "fluent-bit",
        str(chart_input(CHART)),
    ]
    for value in values:
        command.extend(["--set", value])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [resource for resource in yaml.safe_load_all(result.stdout) if resource]


def daemonset(resources):
    return next(resource for resource in resources if resource["kind"] == "DaemonSet")


class FluentBitTests(unittest.TestCase):
    def test_default_positions_database_is_node_local(self):
        resources = render()
        workload = daemonset(resources)
        pod_spec = workload["spec"]["template"]["spec"]
        container, = pod_spec["containers"]
        state_volume = next(volume for volume in pod_spec["volumes"] if volume["name"] == "state")
        state_mount = next(mount for mount in container["volumeMounts"] if mount["name"] == "state")
        config = next(
            resource["data"]["fluent-bit.conf"]
            for resource in resources
            if resource["kind"] == "ConfigMap" and "fluent-bit.conf" in resource.get("data", {})
        )

        self.assertEqual(
            state_volume["hostPath"],
            {"path": "/var/lib/fluent-bit", "type": "DirectoryOrCreate"},
        )
        self.assertEqual(state_mount, {"name": "state", "mountPath": "/buffers"})
        self.assertIn("DB                /buffers/fluent-bit-pos.db", config)
        self.assertNotIn("persistentVolumeClaim", state_volume)
        self.assertFalse(any(resource["kind"] in {"PersistentVolume", "PersistentVolumeClaim"}
                             for resource in resources))

    def test_buffer_paths_are_configurable(self):
        resources = render(
            "bufferStorage.hostPath=/var/lib/kubarr/fluent-bit",
            "bufferStorage.mountPath=/var/run/fluent-bit",
        )
        workload = daemonset(resources)
        pod_spec = workload["spec"]["template"]["spec"]
        container, = pod_spec["containers"]
        state_volume = next(volume for volume in pod_spec["volumes"] if volume["name"] == "state")
        state_mount = next(mount for mount in container["volumeMounts"] if mount["name"] == "state")
        config = next(
            resource["data"]["fluent-bit.conf"]
            for resource in resources
            if resource["kind"] == "ConfigMap" and "fluent-bit.conf" in resource.get("data", {})
        )

        self.assertEqual(state_volume["hostPath"]["path"], "/var/lib/kubarr/fluent-bit")
        self.assertEqual(state_mount["mountPath"], "/var/run/fluent-bit")
        self.assertIn("DB                /var/run/fluent-bit/fluent-bit-pos.db", config)

    def test_security_and_http_probes_are_preserved(self):
        workload = daemonset(render())
        pod_spec = workload["spec"]["template"]["spec"]
        container, = pod_spec["containers"]

        self.assertEqual(pod_spec["securityContext"]["runAsUser"], 0)
        self.assertEqual(pod_spec["securityContext"]["runAsGroup"], 0)
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        for probe_name in ("startupProbe", "readinessProbe", "livenessProbe"):
            self.assertEqual(
                container[probe_name]["httpGet"],
                {"path": "/", "port": "http"},
            )
        self.assertEqual(container["startupProbe"]["periodSeconds"], 2)
        self.assertEqual(container["startupProbe"]["timeoutSeconds"], 1)
        self.assertEqual(container["startupProbe"]["failureThreshold"], 30)


if __name__ == "__main__":
    unittest.main()
