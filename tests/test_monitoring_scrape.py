"""Render and exercise VictoriaMetrics target selection for annotated endpoints."""

import os
import re
import subprocess
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


CHART = ROOT / "monitoring/victoriametrics"


def scrape_jobs():
    output = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", "victoriametrics", str(chart_input(CHART))],
        check=True, capture_output=True, text=True,
    ).stdout
    resources = [item for item in yaml.safe_load_all(output) if item]
    config = next(item["data"]["prometheus.yml"] for item in resources
                  if item["kind"] == "ConfigMap" and "prometheus.yml" in item.get("data", {}))
    return {job["job_name"]: job for job in yaml.safe_load(config)["scrape_configs"]}


def relabel(rules, discovered):
    """Apply the selection/address actions used by these jobs to synthetic SD targets."""
    labels = discovered.copy()
    for rule in rules:
        action = rule.get("action", "replace")
        values = [str(labels.get(name, "")) for name in rule.get("source_labels", [])]
        if action == "keep_if_equal":
            if len(set(values)) != 1:
                return None
            continue
        value = rule.get("separator", ";").join(values)
        match = re.fullmatch(rule.get("regex", "(.*)"), value)
        if action == "keep" and not match or action == "drop" and match:
            return None
        if action == "replace" and match:
            replacement = rule.get("replacement", "$1")
            for index in range(len(match.groups()), 0, -1):
                replacement = replacement.replace(f"${index}", match.group(index) or "")
            labels[rule["target_label"]] = replacement
        elif action not in ("keep", "drop", "replace"):
            raise AssertionError(f"Unhandled relabel action: {action}")
    return labels


class MonitoringScrapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = scrape_jobs()

    def test_multi_port_service_only_scrapes_annotated_port(self):
        rules = self.jobs["kubernetes-services"]["relabel_configs"]
        self.assertEqual(self.jobs["kubernetes-services"]["kubernetes_sd_configs"],
                         [{"role": "service"}])
        self.assertEqual([rule["action"] for rule in rules if "action" in rule].count("keep_if_equal"), 1)
        base = {
            "__meta_kubernetes_service_annotation_prometheus_io_scrape": "true",
            "__meta_kubernetes_service_annotation_prometheus_io_port": "9153",
            "__meta_kubernetes_service_annotation_prometheus_io_path": "/metrics",
            "__meta_kubernetes_namespace": "kube-system",
            "__meta_kubernetes_service_name": "kube-dns",
        }
        for port in (53, 9153):
            with self.subTest(port=port):
                target = relabel(rules, {**base, "__address__": f"kube-dns.kube-system.svc:{port}",
                                         "__meta_kubernetes_service_port_number": str(port)})
                if port == 9153:
                    self.assertEqual(target["__address__"], "kube-dns.kube-system.svc:9153")
                    self.assertEqual(target["__metrics_path__"], "/metrics")
                else:
                    self.assertIsNone(target)
        for annotations in ({"__meta_kubernetes_service_annotation_prometheus_io_port": ""},
                            {"__meta_kubernetes_service_annotation_prometheus_io_port": "9999"},
                            {"__meta_kubernetes_service_annotation_prometheus_io_scrape": "false"}):
            with self.subTest(annotations=annotations):
                self.assertIsNone(relabel(rules, {**base, "__address__": "kube-dns:9153",
                                                   "__meta_kubernetes_service_port_number": "9153",
                                                   **annotations}))

    def test_kube_dns_metrics_egress_is_scoped_to_kube_dns(self):
        output = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "victoriametrics", str(chart_input(CHART))],
            check=True, capture_output=True, text=True,
        ).stdout
        resources = [item for item in yaml.safe_load_all(output) if item]
        policy = next(item for item in resources if item["kind"] == "NetworkPolicy")
        egress = policy["spec"]["egress"]
        dns = next(rule for rule in egress if any(
            peer.get("podSelector", {}).get("matchLabels") == {"k8s-app": "kube-dns"}
            for peer in rule.get("to", [])
        ))
        self.assertEqual(dns["ports"], [
            {"protocol": "UDP", "port": 53},
            {"protocol": "TCP", "port": 53},
            {"protocol": "TCP", "port": 9153},
        ])
        self.assertEqual(len([rule for rule in egress if any(
            peer.get("podSelector", {}).get("matchLabels") == {"k8s-app": "kube-dns"}
            for peer in rule.get("to", [])
        )]), 1)

    def test_exporter_services_own_scrapes_while_other_pods_remain(self):
        pod_rules = self.jobs["kubernetes-pods"]["relabel_configs"]
        service_rules = self.jobs["kubernetes-services"]["relabel_configs"]
        self.assertEqual(self.jobs["kubernetes-pods"]["kubernetes_sd_configs"], [{"role": "pod"}])
        for app in ("radarr", "sonarr"):
            with self.subTest(app=app):
                self.assertIsNone(relabel(pod_rules, {
                    "__meta_kubernetes_pod_annotation_prometheus_io_scrape": "true",
                    "__meta_kubernetes_pod_annotation_prometheus_io_port": "9707",
                    "__meta_kubernetes_namespace": app,
                    "__meta_kubernetes_pod_label_app_kubernetes_io_name": f"{app}-exporter",
                    "__address__": "10.0.0.1:9707",
                }))
                service = relabel(service_rules, {
                    "__meta_kubernetes_service_annotation_prometheus_io_scrape": "true",
                    "__meta_kubernetes_service_annotation_prometheus_io_port": "9707",
                    "__meta_kubernetes_service_port_number": "9707",
                    "__meta_kubernetes_namespace": app,
                    "__meta_kubernetes_service_name": f"{app}-exporter",
                    "__address__": f"{app}-exporter.{app}.svc:9707",
                })
                self.assertEqual(service["app"], app)
        for namespace, name, scrape in (("other", "radarr-exporter", "true"),
                                        ("radarr", "custom-exporter", "true"),
                                        ("radarr", "radarr", "true"),
                                        ("radarr", "radarr-exporter", "false")):
            with self.subTest(namespace=namespace, name=name, scrape=scrape):
                pod = relabel(pod_rules, {
                    "__meta_kubernetes_pod_annotation_prometheus_io_scrape": scrape,
                    "__meta_kubernetes_pod_annotation_prometheus_io_port": "9090",
                    "__meta_kubernetes_namespace": namespace,
                    "__meta_kubernetes_pod_label_app_kubernetes_io_name": name,
                    "__address__": "10.0.0.2:8080",
                })
                if scrape == "true":
                    self.assertEqual(pod["__address__"], "10.0.0.2:9090")
                else:
                    self.assertIsNone(pod)


if __name__ == "__main__":
    unittest.main()
