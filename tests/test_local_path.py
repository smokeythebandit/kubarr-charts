"""The bundled local-path provisioner for clusters without a local default class."""

import json
import os
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input

CHART = ROOT / "system/local-path-provisioner"


def render(*sets):
    command = [os.environ.get("HELM", "helm"), "template", "local-path-provisioner",
               str(chart_input(CHART)), "--namespace", "local-path-storage"]
    for item in sets:
        command += ["--set", item]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def one(docs, kind):
    return [d for d in docs if d["kind"] == kind]


class LocalPathTests(unittest.TestCase):
    def test_chart_metadata(self):
        meta = yaml.safe_load((CHART / "Chart.yaml").read_text())
        self.assertEqual(meta["version"], "0.1.1")
        self.assertEqual(meta["annotations"]["kubarr.io/system"], "true")
        self.assertEqual(meta["annotations"]["kubarr.io/hidden"], "true")

    def test_namespace_is_privileged(self):
        (ns,) = one(render(), "Namespace")
        self.assertEqual(ns["metadata"]["name"], "local-path-storage")
        for mode in ("enforce", "audit", "warn"):
            self.assertEqual(
                ns["metadata"]["labels"][f"pod-security.kubernetes.io/{mode}"],
                "privileged")

    def test_data_path_defaults_under_var(self):
        (cm,) = one(render(), "ConfigMap")
        config = json.loads(cm["data"]["config.json"])
        self.assertEqual(config["nodePathMap"][0]["paths"], ["/var/local-path-provisioner"])

    def test_data_path_is_a_value(self):
        (cm,) = one(render("dataPath=/var/mnt/local-path-provisioner"), "ConfigMap")
        config = json.loads(cm["data"]["config.json"])
        self.assertEqual(config["nodePathMap"][0]["paths"],
                         ["/var/mnt/local-path-provisioner"])

    def test_helper_pod_image_is_pinned(self):
        (cm,) = one(render(), "ConfigMap")
        pod = yaml.safe_load(cm["data"]["helperPod.yaml"])
        image = pod["spec"]["containers"][0]["image"]
        self.assertRegex(image, r":\d+\.\d+")

    def test_provisioner_image_is_pinned(self):
        (dep,) = one(render(), "Deployment")
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertRegex(image, r"rancher/local-path-provisioner:v\d+\.\d+\.\d+$")

    def test_storage_class_defaults(self):
        (sc,) = one(render(), "StorageClass")
        self.assertEqual(sc["metadata"]["name"], "local-path")
        self.assertEqual(sc["provisioner"], "rancher.io/local-path")
        self.assertEqual(sc["volumeBindingMode"], "WaitForFirstConsumer")
        self.assertEqual(sc["reclaimPolicy"], "Delete")
        self.assertNotIn("is-default-class",
                         json.dumps(sc["metadata"].get("annotations", {})))

    def test_storage_class_can_be_default(self):
        (sc,) = one(render("storageClass.default=true"), "StorageClass")
        self.assertEqual(
            sc["metadata"]["annotations"]["storageclass.kubernetes.io/is-default-class"],
            "true")


if __name__ == "__main__":
    unittest.main()
