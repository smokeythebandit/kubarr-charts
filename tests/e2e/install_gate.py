#!/usr/bin/env python3
"""Install and upgrade the charts in a real cluster before they are published.

Rendering is blind to everything that only a running cluster knows. Four charts
were published broken on the same day while every rendering test stayed green:

  1. recyclarr created a private claim whose only workload was a suspended
     CronJob. The default StorageClass binds WaitForFirstConsumer, so nothing
     ever consumed the claim and the install died waiting for a bind that could
     not happen. This harness therefore keeps kind's WaitForFirstConsumer
     local-path class as the cluster default: an Immediate-binding default
     would not reproduce the failure at all.
  2. The platform charts named ghcr.io/smokeythebandit/kubarr-*:0.3.10, a tag
     whose release never published ("manifest unknown"). Only a kubelet that
     tries to pull proves an image exists.
  3. recyclarr's rendered pod violated the `restricted` PodSecurity level its
     own namespace enforces. Admission only happens when a pod is created, so
     the gate creates one -- including for a CronJob, which it triggers.
  4. Upgrades broke as well as installs, so every chart is also upgraded from
     its newest published revision to the local one.

The cluster is the one `kubarr-cli/tests/installer/run.py` builds: a
digest-pinned kind node with PodSecurity defaults, and an external
nfs-server-alpine container on the kind network as the shared media export,
root-squashed and chowned to the media user, after its NFSv4 recovery grace
period has passed. The charts render their own NFS PersistentVolume for the
shared claim, so the harness only has to point them at that fixture
(storage.media.nfs.server).

Usage:
    tests/e2e/install_gate.py                      # charts changed vs origin/main
    tests/e2e/install_gate.py --charts all
    tests/e2e/install_gate.py --charts recyclarr,sonarr
    tests/e2e/install_gate.py --charts changed --base origin/main

Requires: docker, kind, kubectl, helm. The host must be able to serve NFS
(the fixture is a privileged container using the host's nfsd).
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chart_artifacts import DATABASE_CHARTS  # noqa: E402  (tests/ is the package root)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REGISTRY = "oci://ghcr.io/smokeythebandit/kubarr-charts"
# The same pins the installer acceptance run uses, so a failure here and a
# failure there are failures in the same cluster.
KIND_IMAGE = "kindest/node:v1.35.8@sha256:07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0"
NFS_IMAGE = "itsthenetwork/nfs-server-alpine:12"

# Charts that cannot be installed in a bare kind cluster. Every entry is named
# with its reason and is printed in the job summary, so the list cannot grow
# quietly. Anything not listed here is installed for real.
SKIPS = {
    # A Helm library chart: "library charts are not installable" by design.
    # Its templates are exercised through every chart that depends on it.
    "kubarr-common": "library chart; helm refuses to install it",
    # Mounts Cilium's Hubble monitor socket off the host and runs the cilium
    # image as a sidecar; without a Cilium agent on the node the DaemonSet
    # cannot start for reasons that are the cluster's, not the chart's.
    "kubarr-flow-exporter": "needs a Cilium agent and its Hubble socket on the node",
    # Renders cert-manager Certificate/Issuer resources plus an APIService that
    # only resolves once cert-manager has issued its serving certificate.
    # Installing cert-manager first would make this a cert-manager test.
    "kubarr-dns-webhook": "needs cert-manager CRDs and an issued serving certificate",
    # Dials Cloudflare's edge with a real tunnel token from a Secret the user
    # supplies; with a fake token the pod never becomes ready, and with a real
    # one the gate would need a Cloudflare account.
    "cloudflared": "needs a real Cloudflare tunnel token to become ready",
}

# Charts that need another chart installed first. The gate installs these
# prerequisites once per run and leaves them up for the charts that need them.
# Installed in the order listed, and not recursively, so a chart lists its
# prerequisites' own prerequisites first.
PREREQUISITES = {
    # The database is a CloudNativePG Cluster: the operator's CRDs and webhook
    # have to be serving before the chart can be applied at all.
    "postgresql": ["cloudnative-pg"],
    "kubarr-backend": ["cloudnative-pg", "postgresql"],
    "kubarr-worker": ["cloudnative-pg", "postgresql"],
}

# The platform database, as the Kubarr CLI lays it out: the chart bootstraps
# its owner from a basic-auth Secret the CLI writes into the namespace first.
DATABASE_RELEASE = "postgresql"
DATABASE_SECRET = "kubarr-db-app"
DATABASE_USER = "kubarr"
DATABASE_NAME = "kubarr"
DATABASE_CLUSTER = "cluster.postgresql.cnpg.io/kubarr-db"

# Per-chart budget overrides, in seconds, for charts whose images are large or
# whose first start does real work. Everything else uses --budget.
BUDGETS = {
    "immich": 1500,
    "plex": 900,
    "frigate": 900,
    "jellyfin": 900,
    "tdarr": 900,
    "jenkins": 900,
    "redmine": 900,
    "cert-manager": 900,
}

# A pod in one of these waiting states will never recover on its own: fail now,
# with diagnostics, instead of burning the whole budget. "manifest unknown" for
# a tag that was never published lands here within seconds.
TERMINAL_WAITING = {
    "ImagePullBackOff", "ErrImagePull", "InvalidImageName", "RegistryUnavailable",
    "CreateContainerConfigError", "CreateContainerError", "RunContainerError",
}
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")
# Namespaces the gate cleans resources out of but never deletes.
PROTECTED_NAMESPACES = ("default", "kube-system", "kube-public", "kube-node-lease")


class GateFailure(Exception):
    """A chart failed. Carries the text the summary and the log should show."""


def run(*command, timeout=120, check=True, env=None, stdin=None):
    result = subprocess.run([str(arg) for arg in command], env=env, input=stdin,
                            capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{command[0]} failed ({result.returncode}):\n"
                           f"{result.stdout}\n{result.stderr}")
    return result.stdout


def chart_directories():
    return sorted(path.parent for path in ROOT.glob("*/*/Chart.yaml"))


def chart_by_name():
    return {path.name: path for path in chart_directories()}


def values_of(chart):
    path = chart / "values.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def dependents_of(name):
    """Charts whose Chart.yaml lists `name` as a dependency."""
    found = []
    for chart in chart_directories():
        metadata = yaml.safe_load((chart / "Chart.yaml").read_text()) or {}
        if any(dep.get("name") == name for dep in metadata.get("dependencies") or []):
            found.append(chart.name)
    return found


def changed_charts(base):
    """Charts touched against `base`, including uncommitted working-tree edits.

    A change anywhere under the chart directory counts, Chart.yaml included. A
    change to the shared library chart changes every chart that embeds it, so
    its dependents are selected too.
    """
    paths = set()
    merge_base = run("git", "-C", ROOT, "merge-base", base, "HEAD", check=False).strip()
    if merge_base:
        paths.update(run("git", "-C", ROOT, "diff", "--name-only",
                         f"{merge_base}..HEAD").split())
    else:
        print(f"[gate] no merge base with {base}; using the working tree only", flush=True)
    paths.update(run("git", "-C", ROOT, "diff", "--name-only", "HEAD").split())
    paths.update(line[3:].strip().strip('"')
                 for line in run("git", "-C", ROOT, "status", "--porcelain").splitlines()
                 if line[:2] == "??")
    charts = chart_by_name()
    selected = set()
    for path in paths:
        parts = Path(path).parts
        if len(parts) >= 2 and parts[1] in charts and charts[parts[1]].parts[-2] == parts[0]:
            selected.add(parts[1])
    for library in sorted(selected):
        if (charts[library] / "Chart.yaml").is_file():
            metadata = yaml.safe_load((charts[library] / "Chart.yaml").read_text()) or {}
            if metadata.get("type") == "library":
                selected.update(dependents_of(library))
    return sorted(selected)


def container_images(value):
    """Images of real containers, not unrelated `image` configuration."""
    images = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("containers", "initContainers") and isinstance(child, list):
                images.update(container["image"] for container in child if "image" in container)
            else:
                images.update(container_images(child))
    elif isinstance(value, list):
        for child in value:
            images.update(container_images(child))
    return images


def admission_config():
    """kind's own namespaces stay exempt; everything else is at least baseline.

    Charts label their own namespaces (recyclarr enforces `restricted` on
    itself). This default only covers the charts that render no Namespace, so a
    violation is refused there too instead of being merely warned about.
    """
    return {"apiVersion": "apiserver.config.k8s.io/v1", "kind": "AdmissionConfiguration",
            "plugins": [{"name": "PodSecurity", "configuration": {
                "apiVersion": "pod-security.admission.config.k8s.io/v1",
                "kind": "PodSecurityConfiguration",
                "defaults": {"enforce": "baseline", "enforce-version": "latest",
                             "audit": "restricted", "audit-version": "latest",
                             "warn": "restricted", "warn-version": "latest"},
                "exemptions": {"usernames": [], "runtimeClasses": [],
                               "namespaces": ["kube-system", "local-path-storage"]},
            }}]}


def kind_config(admission_path):
    return {"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4",
            "nodes": [{"role": "control-plane", "extraMounts": [
                {"hostPath": str(admission_path),
                 "containerPath": "/etc/kubarr-gate-admission.yaml", "readOnly": True},
            ], "kubeadmConfigPatches": ["""kind: ClusterConfiguration
apiServer:
  extraArgs:
    admission-control-config-file: /etc/kubarr-gate-admission.yaml
  extraVolumes:
    - name: admission
      hostPath: /etc/kubarr-gate-admission.yaml
      mountPath: /etc/kubarr-gate-admission.yaml
      readOnly: true
      pathType: File
"""]}]}


class Gate:
    def __init__(self, args):
        self.args = args
        self.name = "kubarr-gate-" + secrets.token_hex(5)
        self.work = Path(tempfile.mkdtemp(prefix=self.name + "-"))
        self.artifacts = args.artifacts.resolve() / self.name
        self.artifacts.mkdir(parents=True, mode=0o700)
        self.env = dict(os.environ, KUBECONFIG=str(self.work / "kubeconfig"))
        # One password for the platform database and the two charts that read
        # it, exactly as the CLI mints one at bootstrap.
        self.db_password = secrets.token_urlsafe(18)
        self.cluster_created = False
        self.nfs_created = False
        self.volume_created = False
        self.nfs_ip = ""
        self.installed = []
        self.results = []
        self.timings = []
        self.notes = []

    # ---- plumbing ---------------------------------------------------------

    def redact(self, text):
        return text.replace(self.db_password, "[REDACTED]") if self.db_password else text

    def run(self, *command, **kwargs):
        kwargs.setdefault("env", self.env)
        try:
            return run(*command, **kwargs)
        except RuntimeError as error:
            raise RuntimeError(self.redact(str(error))) from None

    def kube(self, *args, **kwargs):
        return self.run("kubectl", "--request-timeout=30s", *args, **kwargs)

    def helm(self, *args, **kwargs):
        return self.run("helm", *args, **kwargs)

    @contextmanager
    def stage(self, name):
        print(f"[gate] {name}", flush=True)
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed = round(time.monotonic() - start, 1)
            self.timings.append({"stage": name, "seconds": elapsed})
            (self.artifacts / "timings.json").write_text(json.dumps(self.timings, indent=2))
            print(f"[gate] {name}: {elapsed}s", flush=True)

    # ---- fixture ----------------------------------------------------------

    def source_tree(self):
        """A throwaway copy of the repository.

        `helm dependency build` writes into the chart directory and can rewrite
        Chart.lock. The gate must never dirty the tree it is testing, and the
        file:// dependency on system/kubarr-common is relative, so the whole
        tree is copied rather than single charts.
        """
        copy = self.work / "charts-src"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", "artifacts"))
        return copy

    def prepare_chart(self, name):
        chart = self.tree_charts[name]
        metadata = yaml.safe_load((chart / "Chart.yaml").read_text()) or {}
        if metadata.get("dependencies"):
            self.helm("dependency", "build", chart, timeout=180)
        return chart

    def pull_image(self, image):
        """Pre-pull so the budget measures the chart, not the public internet.

        A pull that fails is not fatal here: the node is left to try it itself,
        which is how a tag that was never published has to be caught -- as an
        ImagePullBackOff on a real pod, with the kubelet's own message.
        """
        inspected = self.run("docker", "image", "inspect", image, check=False)
        if inspected.strip() and inspected.strip() != "[]":
            return image
        try:
            self.run("docker", "pull", image, timeout=900)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            first = str(error).strip().splitlines()[-1:] or [""]
            print(f"[gate] cannot pre-pull {image}: {first[0]}", flush=True)
            self.notes.append(f"image not pre-pulled: {image}")
            return None
        return image

    def load_images(self, images):
        if not images:
            return
        platform = self.run("docker", "version",
                            "--format", "{{.Server.Os}}/{{.Server.Arch}}").strip()
        # Digest-pinned references lose their digest through `docker save`, so
        # the node pulls those itself rather than importing a dangling alias.
        in_node = [image for image in images if "@sha256:" in image]
        archived = [image for image in images if "@sha256:" not in image]
        # In batches: a forced full run covers forty-odd charts, and one archive
        # of every image at once is tens of gigabytes of temporary file.
        batch_size = 8
        batches = [archived[index:index + batch_size]
                   for index in range(0, len(archived), batch_size)]
        while batches:
            batch = batches.pop(0)
            archive = self.work / "images.tar"
            try:
                self.run("docker", "image", "save", "--platform", platform,
                         "-o", archive, *batch, timeout=900)
            except RuntimeError as error:
                # Docker's containerd image store refuses to export some pulled
                # images by platform even though they run and are complete in
                # the registry. Let the node pull those, and never drop one.
                rejected = re.findall(r"image with reference (\S+) was found but does not provide",
                                      str(error))
                remaining = [image for image in batch if image not in rejected]
                if not rejected or remaining == batch:
                    raise
                print(f"[gate] docker cannot export {' '.join(rejected)}; "
                      "pulling in the node instead", flush=True)
                in_node += rejected
                if remaining:
                    batches.insert(0, remaining)
                continue
            self.run("kind", "load", "image-archive", "--name", self.name, archive, timeout=900)
            archive.unlink()
        for image in in_node:
            self.run("docker", "exec", self.name + "-control-plane",
                     "crictl", "pull", image, timeout=600, check=False)

    def setup(self, targets):
        with self.stage("Copy the tree and build chart dependencies"):
            tree = self.source_tree()
            self.tree_charts = {path.parent.name: path.parent
                                for path in tree.glob("*/*/Chart.yaml")}
            needed = sorted(set(targets) | {chart for target in targets
                                            for chart in PREREQUISITES.get(target, [])})
            for name in needed:
                self.prepare_chart(name)
            self.needed = needed

        with self.stage("Create the kind cluster"):
            admission = self.work / "admission.yaml"
            admission.write_text(yaml.safe_dump(admission_config()))
            config = self.work / "kind.yaml"
            config.write_text(yaml.safe_dump(kind_config(admission)))
            self.cluster_created = True
            self.run("kind", "create", "cluster", "--name", self.name,
                     "--kubeconfig", self.env["KUBECONFIG"], "--image", KIND_IMAGE,
                     "--config", config, "--retain", "--wait", "180s", timeout=420)
            if self.kube("config", "current-context").strip() != f"kind-{self.name}":
                raise RuntimeError("Unexpected context in the isolated kubeconfig")
            self.assert_wait_for_first_consumer()

        with self.stage("Start the external NFS export"):
            self.pull_image(NFS_IMAGE)
            self.run("docker", "volume", "create", "--label",
                     f"kubarr.gate.run={self.name}", self.name)
            self.volume_created = True
            self.run("docker", "run", "-d", "--name", self.name + "-nfs", "--network", "kind",
                     "--label", f"kubarr.gate.run={self.name}", "--privileged",
                     "-v", f"{self.name}:/nfsshare", "-e", "SHARED_DIRECTORY=/nfsshare",
                     NFS_IMAGE, timeout=120)
            self.nfs_created = True
            self.nfs_ip = self.run("docker", "inspect", "--format",
                                   '{{(index .NetworkSettings.Networks "kind").IPAddress}}',
                                   self.name + "-nfs").strip()
            self.wait_nfs_grace()
            self.configure_nfs_permissions()
            print(f"[gate] shared export at {self.nfs_ip}:/", flush=True)

        with self.stage("Pre-pull and load chart images"):
            images = set()
            for name in self.needed:
                try:
                    images.update(self.chart_images(name))
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    # One chart that will not render must not cost the whole
                    # run: let its own turn report the template error.
                    print(f"[gate] cannot read {name}'s images: {error}", flush=True)
                    self.notes.append(f"{name}: images unknown; it does not render")
            with ThreadPoolExecutor(max_workers=4) as pool:
                pulled = [image for image in pool.map(self.pull_image, sorted(images)) if image]
            self.load_images(pulled)

    def assert_wait_for_first_consumer(self):
        """The failure class in criterion 1 only exists under late binding."""
        classes = json.loads(self.kube("get", "storageclass", "-o", "json"))["items"]
        default = [item for item in classes
                   if (item["metadata"].get("annotations") or {}).get(
                       "storageclass.kubernetes.io/is-default-class") == "true"]
        if len(default) != 1 or default[0].get("volumeBindingMode") != "WaitForFirstConsumer":
            raise RuntimeError(
                "The gate needs exactly one default StorageClass binding "
                "WaitForFirstConsumer (kind's local-path is one); found: "
                + json.dumps([(item["metadata"]["name"], item.get("volumeBindingMode"))
                              for item in default]))
        print(f"[gate] default StorageClass {default[0]['metadata']['name']} "
              "binds WaitForFirstConsumer", flush=True)

    def wait_nfs_grace(self):
        # A fresh kernel NFSv4 server refuses new stateful operations during its
        # recovery grace period; a mount attempted inside it hangs.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            ready = self.run("docker", "exec", self.name + "-nfs", "sh", "-c",
                             "cat /proc/fs/nfsd/v4_end_grace",
                             timeout=20, check=False).strip()
            if ready == "Y":
                return
            time.sleep(2)
        raise RuntimeError("The NFS fixture never finished its recovery grace period")

    def configure_nfs_permissions(self):
        # A normal external share: the media user owns it, and kubelet's root
        # identity is squashed, so the charts' own initContainers have to create
        # their subdirectories as the application's uid.
        exports = self.run("docker", "exec", self.name + "-nfs", "sh", "-ec",
                           "chown 1000:1000 /nfsshare; chmod 0775 /nfsshare; "
                           "sed -i 's/no_root_squash/root_squash/g' /etc/exports; "
                           "exportfs -ra; exportfs -v", timeout=30)
        if "no_root_squash" in exports or "root_squash" not in exports:
            raise RuntimeError("The NFS fixture did not enable root squashing")

    # ---- chart knowledge --------------------------------------------------

    def chart_args(self, name, upgrade=False):
        """What a chart needs before it will install at all.

        Everything here is derived from the chart's own values, so a new chart
        needs no entry: a shared export is pointed at the fixture, the platform
        database charts are handed the password they deliberately ship without,
        and a private-config upgrade is handed the migration acknowledgement
        the charts require on purpose (storage.config.migrationConfirmed).
        """
        values = values_of(self.tree_charts[name])
        storage = values.get("storage") or {}
        args = []
        if isinstance(storage.get("media"), dict):
            args += ["--set", f"storage.media.nfs.server={self.nfs_ip}",
                     "--set", "storage.media.nfs.path=/",
                     "--set", "storage.media.nfs.size=8Gi"]
        if name == DATABASE_RELEASE:
            # The chart takes no password, only the name of the Secret that
            # ensure_database_secret wrote; the password never reaches Helm.
            args += ["--set", f"database.existingSecret={DATABASE_SECRET}"]
        elif name in DATABASE_CHARTS:
            args += ["--set", f"database.password={self.db_password}"]
        if upgrade and isinstance(storage.get("config"), dict):
            # Not a failure to work around: the charts refuse a private-config
            # upgrade until the operator confirms the data was migrated.
            args += ["--set", "storage.config.migrationConfirmed=true"]
        # Overrides a human adds on the command line, so a suspected failure can
        # be reproduced against an unmodified chart.
        for override in self.args.set:
            args += ["--set", override]
        return args

    def chart_namespace(self, name):
        values = values_of(self.tree_charts[name])
        return ((values.get("namespace") or {}).get("name")) or name

    def rendered(self, name, upgrade=False):
        namespace = self.chart_namespace(name)
        output = self.helm("template", name, self.tree_charts[name], "-n", namespace,
                           *self.chart_args(name, upgrade), timeout=120)
        return [doc for doc in yaml.safe_load_all(output) if doc]

    def chart_images(self, name):
        return container_images(self.rendered(name))

    def budget(self, name):
        return BUDGETS.get(name, self.args.budget)

    # ---- the gate ---------------------------------------------------------

    def ensure_database_secret(self, namespace):
        """Write the database credential before the database chart installs.

        The same document the CLI applies: the namespace carries Helm's
        ownership metadata, so the chart's own Namespace adopts it instead of
        refusing to install over it, and the Secret is basic-auth because the
        Cluster's initdb bootstrap requires one. Its password is the one the
        backend and worker charts are handed, so their URIs log in. Applied
        from stdin so the password never appears in an argument list.
        """
        uri = (f"postgresql://{DATABASE_USER}:{self.db_password}@kubarr-db-rw."
               f"{namespace}.svc.cluster.local:5432/{DATABASE_NAME}")
        documents = [
            {"apiVersion": "v1", "kind": "Namespace",
             "metadata": {"name": namespace,
                          "labels": {"app.kubernetes.io/managed-by": "Helm"},
                          "annotations": {"meta.helm.sh/release-name": DATABASE_RELEASE,
                                          "meta.helm.sh/release-namespace": namespace}}},
            {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/basic-auth",
             "metadata": {"name": DATABASE_SECRET, "namespace": namespace},
             "stringData": {"username": DATABASE_USER, "password": self.db_password,
                            "uri": uri}},
        ]
        self.kube("apply", "-f", "-", stdin=yaml.safe_dump_all(documents))

    def install(self, name, chart, namespace, budget, label, upgrade=False, wait=True,
                scope=None):
        if name == DATABASE_RELEASE:
            # Every phase starts from a namespace the previous one deleted.
            self.ensure_database_secret(namespace)
        action = "upgrade" if upgrade else "install"
        command = ["helm", action, name, str(chart), "-n", namespace,
                   *self.chart_args(name, upgrade=upgrade)]
        if not upgrade:
            command.append("--create-namespace")
        if wait:
            command += ["--wait", f"--timeout={budget}s"]
        print(f"[gate] {label}: {' '.join(self.redact(part) for part in command)}", flush=True)
        if not wait:
            self.helm(*command[1:], timeout=budget + 60)
            return
        # helm --wait is the contract, but it reports nothing until it gives up.
        # Watch the namespace alongside it so a pod that can never start, or a
        # claim nothing will consume, fails in seconds with its own diagnosis.
        process = subprocess.Popen([str(part) for part in command], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, start_new_session=True)
        deadline = time.monotonic() + budget + 60
        verdict = None
        while process.poll() is None:
            if time.monotonic() > deadline:
                verdict = f"{action} exceeded its {budget}s budget and helm did not return"
                break
            verdict = self.terminal_failure(namespace, scope)
            if verdict:
                break
            time.sleep(5)
        if verdict:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            raise GateFailure(f"{label} failed: {verdict}")
        output = (process.stdout.read() or "").strip()
        if process.returncode:
            raise GateFailure(f"{label} failed: helm {action} exited "
                              f"{process.returncode}\n{self.redact(output)}")

    def terminal_failure(self, namespace, scope=None):
        """A condition no amount of waiting will clear.

        `scope` keeps an upgrade honest. The revision being upgraded from may
        itself be broken -- that is what happened -- so its leftover pods and
        its own claims must not be read as failures of the chart under test.
        Only pods this release created and claims this chart renders count.
        """
        ignore_pods = (scope or {}).get("ignore_pods") or set()
        own_claims = (scope or {}).get("claims")
        try:
            pods = json.loads(self.kube("-n", namespace, "get", "pods", "-o", "json"))["items"]
            claims = json.loads(self.kube("-n", namespace, "get", "pvc", "-o", "json"))["items"]
            events = json.loads(self.kube("-n", namespace, "get", "events",
                                          "-o", "json"))["items"]
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            return None
        pods = [pod for pod in pods if pod["metadata"]["uid"] not in ignore_pods]
        for pod in pods:
            statuses = (pod["status"].get("containerStatuses") or []) + \
                       (pod["status"].get("initContainerStatuses") or [])
            for status in statuses:
                waiting = (status.get("state") or {}).get("waiting") or {}
                reason = waiting.get("reason")
                if reason in TERMINAL_WAITING:
                    return (f"pod {pod['metadata']['name']} container {status['name']} is "
                            f"{reason}: {waiting.get('message', '').strip()}")
                if reason == "CrashLoopBackOff" and status.get("restartCount", 0) >= 3:
                    return (f"pod {pod['metadata']['name']} container {status['name']} has "
                            f"crash-looped {status['restartCount']} times")
        # A pod that admission refused is never created at all; the controller
        # that tried says so in an event. This is criterion 3's signature.
        for event in events:
            message = event.get("message", "")
            if event.get("reason") in ("FailedCreate", "FailedCreatePodSandBox") and (
                    "violates PodSecurity" in message or "forbidden" in message):
                return (f"{event['involvedObject'].get('kind')} "
                        f"{event['involvedObject'].get('name')} cannot create its pod: "
                        f"{message.strip()}")
        # The recyclarr failure: a claim left Pending with no pod that could
        # ever consume it under WaitForFirstConsumer binding.
        consumed = {volume.get("persistentVolumeClaim", {}).get("claimName")
                    for pod in pods for volume in (pod["spec"].get("volumes") or [])
                    if volume.get("persistentVolumeClaim")}
        for claim in claims:
            if claim["status"].get("phase") == "Bound" or claim["metadata"]["name"] in consumed:
                continue
            if own_claims is not None and claim["metadata"]["name"] not in own_claims:
                continue
            age = self.age_seconds(claim["metadata"].get("creationTimestamp"))
            if age is not None and age > self.args.claim_grace:
                reasons = [f"{event.get('reason')}: {event.get('message')}" for event in events
                           if event["involvedObject"].get("kind") == "PersistentVolumeClaim"
                           and event["involvedObject"].get("name") == claim["metadata"]["name"]]
                return (f"PersistentVolumeClaim {claim['metadata']['name']} has been "
                        f"{claim['status'].get('phase')} for {int(age)}s and no pod in "
                        f"{namespace} consumes it, so a WaitForFirstConsumer class will "
                        f"never bind it. {' | '.join(reasons[-3:])}")
        return None

    @staticmethod
    def age_seconds(stamp):
        """Seconds since an RFC 3339 UTC timestamp as the API server writes it."""
        if not stamp:
            return None
        return (datetime.now(timezone.utc)
                - datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)).total_seconds()

    def assert_ready(self, name, namespace, budget, upgrade=False, scope=None):
        """Every workload the chart renders has to actually run.

        helm --wait covers Deployments, StatefulSets and DaemonSets. It does not
        cover a CronJob, which creates no pod until its schedule fires -- and
        none at all while suspended, which is how recyclarr ships. A workload
        whose pod is never created is a workload whose image, security context
        and mounts were never tested, so the gate creates one.
        """
        docs = self.rendered(name, upgrade=upgrade)
        checked = []
        # helm --wait knows neither of these. A CRD must be served before a
        # dependent chart applies its kind, and the database Cluster must be
        # Ready before its backup job or any client can connect, so both are
        # settled before the workloads below are judged.
        for doc in docs:
            kind, meta = doc["kind"], doc["metadata"]
            if kind == "CustomResourceDefinition":
                self.assert_condition(f"crd/{meta['name']}", "Established", None, budget)
                checked.append(f"CustomResourceDefinition/{meta['name']}")
            elif kind == "Cluster" and doc["apiVersion"].startswith("postgresql.cnpg.io/"):
                target = meta.get("namespace") or namespace
                self.assert_condition(f"cluster.postgresql.cnpg.io/{meta['name']}", "Ready",
                                      target, budget, scope)
                checked.append(f"Cluster/{meta['name']}")
        for doc in docs:
            kind, meta = doc["kind"], doc["metadata"]
            target = meta.get("namespace") or namespace
            if kind in WORKLOAD_KINDS:
                self.assert_rollout(kind, meta["name"], target, budget)
                checked.append(f"{kind}/{meta['name']}")
            elif kind == "CronJob":
                self.assert_cronjob(meta["name"], target, budget, scope)
                checked.append(f"CronJob/{meta['name']}")
            elif kind == "PersistentVolumeClaim":
                phase = json.loads(self.kube("-n", target, "get", "pvc", meta["name"],
                                             "-o", "json"))["status"].get("phase")
                if phase != "Bound":
                    raise GateFailure(f"PersistentVolumeClaim {meta['name']} in {target} "
                                      f"is {phase}, not Bound")
                checked.append(f"PersistentVolumeClaim/{meta['name']}")
        if not checked:
            # Some charts are configuration only (kubarr-cilium-identities
            # renders a ConfigMap, and its CiliumClusterwideNetworkPolicy is
            # guarded by an APIVersions check that kindnet does not satisfy).
            # There is no readiness to wait for, so the assertion is that every
            # object it rendered is really in the cluster.
            for doc in docs:
                target = doc["metadata"].get("namespace") or namespace
                where = () if doc["kind"] in ("Namespace", "PersistentVolume",
                                              "ClusterRole", "ClusterRoleBinding",
                                              "CustomResourceDefinition") else ("-n", target)
                self.kube(*where, "get", doc["kind"].lower(), doc["metadata"]["name"],
                          "-o", "name")
                checked.append(f"{doc['kind']}/{doc['metadata']['name']}")
            self.notes.append(f"{name}: renders no workload; the gate asserted that its "
                              f"{len(checked)} object(s) exist, with no pod to make ready")
        if not checked:
            raise GateFailure(f"{name} rendered nothing at all")
        print(f"[gate] ready: {', '.join(checked)}", flush=True)

    def assert_condition(self, resource, condition, namespace, budget, scope=None):
        """Wait for a status condition, failing early on a pod that cannot start."""
        where = ("-n", namespace) if namespace else ()
        deadline = time.monotonic() + budget
        while True:
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                raise GateFailure(f"{resource} never reached {condition} within {budget}s")
            try:
                self.kube(*where, "wait", f"--for=condition={condition}", resource,
                          f"--timeout={min(remaining, 30)}s", timeout=min(remaining, 30) + 30)
                return
            except (RuntimeError, subprocess.TimeoutExpired):
                # Also what `kubectl wait` says while the object does not exist
                # yet (the operator creates nothing before its webhook serves).
                time.sleep(5)
            if namespace:
                verdict = self.terminal_failure(namespace, scope)
                if verdict:
                    raise GateFailure(f"{resource} in {namespace}: {verdict}")

    def assert_rollout(self, kind, workload, namespace, budget):
        try:
            self.kube("-n", namespace, "rollout", "status", f"{kind.lower()}/{workload}",
                      f"--timeout={budget}s", timeout=budget + 30)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            raise GateFailure(f"{kind}/{workload} in {namespace} never rolled out: {error}")
        live = json.loads(self.kube("-n", namespace, "get", kind.lower(), workload, "-o", "json"))
        status = live.get("status") or {}
        if kind == "DaemonSet":
            desired, ready = status.get("desiredNumberScheduled", 0), status.get("numberReady", 0)
        else:
            desired = live["spec"].get("replicas", 1)
            ready = status.get("readyReplicas", 0)
        # `rollout status` calls a workload that wants no pods complete, so a
        # nodeSelector or a replica count that schedules nothing passes it.
        if desired < 1 or ready < desired:
            raise GateFailure(f"{kind}/{workload} in {namespace} has {ready}/{desired} ready")

    def assert_cronjob(self, workload, namespace, budget, scope=None):
        job = f"{workload}-gate"
        self.kube("-n", namespace, "create", "job", job, f"--from=cronjob/{workload}")
        deadline = time.monotonic() + budget
        started = False
        while time.monotonic() < deadline:
            verdict = self.terminal_failure(namespace, scope)
            if verdict:
                raise GateFailure(f"CronJob/{workload} in {namespace}: {verdict}")
            live = json.loads(self.kube("-n", namespace, "get", "job", job, "-o", "json"))
            conditions = {item["type"]: item for item in (live["status"].get("conditions") or [])
                          if item["status"] == "True"}
            pods = json.loads(self.kube("-n", namespace, "get", "pods",
                                        "-l", f"batch.kubernetes.io/job-name={job}",
                                        "-o", "json"))["items"]
            # Admission and image pulls are what a CronJob never exercises on
            # its own; record that its pod really ran before judging the result.
            started = started or any(
                (status.get("state") or {}).get("running") or (status.get("state") or {}).get("terminated")
                for pod in pods for status in (pod["status"].get("containerStatuses") or []))
            if "Complete" in conditions:
                self.kube("-n", namespace, "delete", "job", job, "--wait=false", check=False)
                return
            if "Failed" in conditions:
                logs = self.kube("-n", namespace, "logs", f"job/{job}", "--tail=40",
                                 check=False, timeout=60)
                raise GateFailure(f"Job from CronJob/{workload} in {namespace} failed: "
                                  f"{conditions['Failed'].get('message')}\n{logs}")
            time.sleep(5)
        raise GateFailure(f"Job from CronJob/{workload} in {namespace} did not finish within "
                          f"{budget}s (pod started: {started})")

    @staticmethod
    def claim_names(docs):
        return {doc["metadata"]["name"] for doc in docs
                if doc["kind"] == "PersistentVolumeClaim"}

    def pod_uids(self, namespace):
        """The pods already there, which the next phase did not create."""
        try:
            pods = json.loads(self.kube("-n", namespace, "get", "pods", "-o", "json"))["items"]
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            return set()
        return {pod["metadata"]["uid"] for pod in pods}

    def published_version(self, name):
        """The newest revision in the registry: the one a user is upgrading from."""
        try:
            shown = self.helm("show", "chart", f"{REGISTRY}/{name}", timeout=180)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            print(f"[gate] no published revision of {name}: {error}", flush=True)
            return None
        for line in shown.splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
        return None

    def uninstall(self, name, namespace, docs):
        self.helm("uninstall", name, "-n", namespace, "--wait",
                  "--timeout=300s", timeout=360, check=False)
        # The charts annotate their claims and shared volumes `resource-policy:
        # keep`, so uninstall leaves them behind. A Retain PersistentVolume left
        # over from the previous phase goes Released and can never be re-bound,
        # which would make the next phase fail for the harness's reasons rather
        # than the chart's.
        if namespace not in PROTECTED_NAMESPACES:
            self.kube("delete", "namespace", namespace, "--wait=true", "--timeout=180s",
                      check=False, timeout=240)
        volumes = [doc["metadata"]["name"] for doc in docs if doc["kind"] == "PersistentVolume"]
        if volumes:
            self.kube("delete", "persistentvolume", *volumes, "--wait=false",
                      check=False, timeout=120)
        if name in self.installed:
            self.installed.remove(name)

    def prerequisites(self, name):
        for prerequisite in PREREQUISITES.get(name, []):
            if prerequisite in self.installed:
                continue
            namespace = self.chart_namespace(prerequisite)
            print(f"[gate] {name} needs {prerequisite}; installing it first", flush=True)
            self.install(prerequisite, self.tree_charts[prerequisite], namespace,
                         self.budget(prerequisite), f"prerequisite {prerequisite}")
            self.assert_ready(prerequisite, namespace, self.budget(prerequisite))
            self.installed.append(prerequisite)

    def check(self, name):
        namespace = self.chart_namespace(name)
        budget = self.budget(name)
        chart = self.tree_charts[name]
        record = {"chart": name, "namespace": namespace, "budget": budget,
                  "install": "not run", "upgrade": "not run", "seconds": {}}
        try:
            docs = self.rendered(name)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            record["install"] = record["upgrade"] = self.redact(f"does not render: {error}")
            print(f"[gate] FAIL {name}: {record['install']}", flush=True)
            self.results.append(record)
            return False
        scope = {"claims": self.claim_names(docs)}
        # A chart that is also someone else's prerequisite is already installed
        # by the time its own turn comes; the fresh install has to be fresh.
        if name in self.installed:
            self.uninstall(name, namespace, docs)
        try:
            self.prerequisites(name)
        except (GateFailure, RuntimeError, subprocess.TimeoutExpired) as error:
            record["install"] = record["upgrade"] = f"prerequisite failed: {error}"
            self.results.append(record)
            return False

        with self.stage(f"{name}: fresh install"):
            start = time.monotonic()
            try:
                self.install(name, chart, namespace, budget, f"{name} install", scope=scope)
                self.assert_ready(name, namespace, budget, scope=scope)
                record["install"] = "pass"
            except (GateFailure, RuntimeError, subprocess.TimeoutExpired) as error:
                record["install"] = self.redact(str(error))
                print(f"[gate] FAIL {name} install: {record['install']}", flush=True)
                self.diagnose(name, namespace, "install")
            record["seconds"]["install"] = round(time.monotonic() - start, 1)
            self.uninstall(name, namespace, docs)

        if record["install"] != "pass":
            self.results.append(record)
            return False

        version = self.published_version(name)
        if not version:
            record["upgrade"] = "skipped: never published, so there is nothing to upgrade from"
            self.notes.append(f"{name}: no published revision to upgrade from")
            self.results.append(record)
            return True

        with self.stage(f"{name}: upgrade from published {version}"):
            start = time.monotonic()
            record["from"] = version
            try:
                published = self.work / "published" / name
                published.mkdir(parents=True, exist_ok=True)
                self.helm("pull", f"{REGISTRY}/{name}", "--version", version,
                          "--destination", published, timeout=300)
                package, = published.glob("*.tgz")
                # The baseline is applied without waiting on purpose: the gate
                # is asserting the upgrade's result, and a published revision
                # whose pods are slow or unhealthy must not consume the budget
                # of the revision under test.
                try:
                    self.install(name, package, namespace, budget,
                                 f"{name} baseline {version}", wait=False)
                except (GateFailure, RuntimeError, subprocess.TimeoutExpired) as error:
                    # The published revision cannot even be applied, which the
                    # gate reports rather than blames on the local chart: if
                    # nobody can have that revision installed, nobody is
                    # upgrading from it, and the fresh install above already
                    # proved the local chart stands up on its own.
                    record["upgrade"] = self.redact(
                        f"baseline-broken: published {version} cannot install, so there is no "
                        f"upgrade path to exercise: {error}")
                    print(f"[gate] WARN {name}: {record['upgrade']}", flush=True)
                    raise GateFailure("baseline") from None
                local = self.rendered(name, upgrade=True)
                scope = {"claims": self.claim_names(local),
                         "ignore_pods": self.pod_uids(namespace)}
                self.install(name, chart, namespace, budget,
                             f"{name} upgrade to local", upgrade=True, scope=scope)
                self.assert_ready(name, namespace, budget, upgrade=True, scope=scope)
                record["upgrade"] = "pass"
            except (GateFailure, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
                if not record["upgrade"].startswith("baseline-broken:"):
                    record["upgrade"] = self.redact(str(error))
                    print(f"[gate] FAIL {name} upgrade: {record['upgrade']}", flush=True)
                    self.diagnose(name, namespace, "upgrade")
                else:
                    self.notes.append(f"{name}: published {version} cannot be installed; "
                                      "the upgrade path could not be exercised")
            record["seconds"]["upgrade"] = round(time.monotonic() - start, 1)
            self.uninstall(name, namespace, self.rendered(name, upgrade=True))

        self.results.append(record)
        return record["upgrade"].startswith(("pass", "baseline-broken:"))

    # ---- diagnostics ------------------------------------------------------

    def diagnose(self, name, namespace, phase):
        """A gate nobody can debug gets disabled. Print the evidence inline."""
        directory = self.artifacts / f"{name}-{phase}"
        directory.mkdir(parents=True, exist_ok=True)
        print(f"\n===== {name} ({phase}) diagnostics =====", flush=True)
        blocks = {
            "pods.txt": ("-n", namespace, "get", "pods,pvc,jobs", "-o", "wide"),
            "events.txt": ("-n", namespace, "get", "events",
                           "--sort-by=.metadata.creationTimestamp"),
        }
        for filename, command in blocks.items():
            text = self.kube(*command, check=False, timeout=60)
            (directory / filename).write_text(self.redact(text))
            print(f"--- kubectl {' '.join(command)} ---\n{self.redact(text)}", flush=True)
        try:
            pods = json.loads(self.kube("-n", namespace, "get", "pods", "-o", "json"))["items"]
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            pods = []
        for pod in pods:
            unready = [condition for condition in (pod["status"].get("conditions") or [])
                       if condition["type"] == "Ready" and condition["status"] != "True"]
            if pod["status"].get("phase") == "Running" and not unready:
                continue
            pod_name = pod["metadata"]["name"]
            described = self.kube("-n", namespace, "describe", "pod", pod_name,
                                  check=False, timeout=60)
            (directory / f"describe-{pod_name}.txt").write_text(self.redact(described))
            print(f"--- kubectl describe pod {pod_name} ---\n{self.redact(described)}", flush=True)
            for container in ((pod["spec"].get("initContainers") or []) +
                              (pod["spec"].get("containers") or [])):
                logs = self.kube("-n", namespace, "logs", pod_name, "-c", container["name"],
                                 "--tail=60", check=False, timeout=60)
                if logs.strip():
                    (directory / f"logs-{pod_name}-{container['name']}.txt").write_text(
                        self.redact(logs))
                    print(f"--- logs {pod_name}/{container['name']} ---\n"
                          f"{self.redact(logs)}", flush=True)
        print(f"===== end {name} ({phase}) =====\n", flush=True)

    def summary(self, targets, skipped):
        def ok(record):
            return record["install"] == "pass" and (
                record["upgrade"] == "pass"
                or record["upgrade"].startswith(("skipped", "baseline-broken:")))

        passed = [record for record in self.results if ok(record)]
        failed = [record for record in self.results if not ok(record)]
        missing = [name for name in targets
                   if name not in {record["chart"] for record in self.results}]
        lines = ["## Chart install gate", "",
                 f"Cluster `{self.name}` | kind image `{KIND_IMAGE.split('@')[0]}` | "
                 f"shared export `{self.nfs_ip}:/`", "",
                 "| Chart | Install | Upgrade from | Upgrade | Seconds |",
                 "| --- | --- | --- | --- | --- |"]
        for record in sorted(self.results, key=lambda item: item["chart"]):
            seconds = "+".join(str(value) for value in record["seconds"].values()) or "-"
            lines.append(f"| `{record['chart']}` | {self.cell(record['install'])} | "
                         f"`{record.get('from', '-')}` | {self.cell(record['upgrade'])} | "
                         f"{seconds} |")
        # The whole skip list is printed on every run, not only the entries
        # this selection touched, so it cannot grow unnoticed.
        lines += ["", f"**Charts skipped by name ({len(SKIPS)} in total, "
                      f"{len(skipped)} in this selection):**", ""]
        for name, reason in sorted(SKIPS.items()):
            mark = " (selected)" if name in skipped else ""
            lines.append(f"- `{name}`{mark} - {reason}")
        if self.notes:
            lines += ["", "**Notes:**", ""] + [f"- {note}" for note in dict.fromkeys(self.notes)]
        total = round(sum(item["seconds"] for item in self.timings), 1)
        if missing:
            lines += ["", f"**Never reached ({len(missing)}):** "
                          + ", ".join(f"`{name}`" for name in missing)]
        lines += ["", f"Selected {len(targets)} chart(s); {len(passed)} passed, "
                      f"{len(failed) + len(missing)} failed; total {total}s."]
        text = "\n".join(lines)
        print("\n" + text, flush=True)
        (self.artifacts / "summary.md").write_text(text)
        (self.artifacts / "results.json").write_text(json.dumps(
            {"run": self.name, "targets": targets, "skipped": skipped,
             "results": self.results, "timings": self.timings}, indent=2))
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a") as handle:
                handle.write(text + "\n")
        return not failed and not missing

    @staticmethod
    def cell(value):
        if value == "pass":
            return "pass"
        if value.startswith("skipped") or value == "not run":
            return value
        if value.startswith("baseline-broken:"):
            return "**WARN** " + " ".join(value.strip().splitlines())[:300].replace("|", "/")
        # helm prints klog warnings before the error it died of; the table has
        # room for one line, so give it the one that says what went wrong.
        lines = [line.strip() for line in value.strip().splitlines() if line.strip()]
        errors = [line for line in lines if line.startswith("Error:") or "failed:" in line]
        first = " ".join(errors or lines)[:300]
        return f"**FAIL** {first.replace('|', '/')}"

    def cleanup(self):
        if self.cluster_created and not self.args.keep:
            self.run("kind", "delete", "cluster", "--name", self.name, timeout=300, check=False)
        if self.nfs_created and not self.args.keep:
            self.run("docker", "rm", "-f", self.name + "-nfs", timeout=60, check=False)
        if self.volume_created and not self.args.keep:
            self.run("docker", "volume", "rm", self.name, timeout=60, check=False)
        if self.args.keep:
            print(f"[gate] --keep: cluster {self.name} and {self.work} left in place", flush=True)
            return
        shutil.rmtree(self.work, ignore_errors=True)


def resolve_targets(args):
    charts = chart_by_name()
    if args.charts == "all":
        selected = sorted(charts)
    elif args.charts == "changed":
        selected = changed_charts(args.base)
    else:
        selected = [name.strip() for name in args.charts.split(",") if name.strip()]
        unknown = [name for name in selected if name not in charts]
        if unknown:
            raise SystemExit(f"[gate] no such chart: {', '.join(unknown)}")
    skipped = {name: reason for name, reason in SKIPS.items() if name in selected}
    return [name for name in selected if name not in skipped], skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--charts", default="changed",
                        help="'changed' (default), 'all', or a comma-separated list")
    parser.add_argument("--base", default="origin/main",
                        help="Git ref that 'changed' compares against")
    parser.add_argument("--budget", type=int, default=600,
                        help="Seconds a single chart gets to install or upgrade")
    parser.add_argument("--claim-grace", type=int, default=90,
                        help="Seconds an unconsumed claim may stay Pending before it is a failure")
    parser.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                        help="Extra helm --set passed to every chart, repeatable; for "
                             "reproducing a failure without editing a chart")
    parser.add_argument("--artifacts", type=Path, default=HERE / "artifacts")
    parser.add_argument("--keep", action="store_true",
                        help="Leave the cluster and the NFS fixture up for debugging")
    args = parser.parse_args()

    for tool in ("docker", "kind", "kubectl", "helm"):
        if not shutil.which(tool):
            parser.error(f"Missing tool: {tool}")

    targets, skipped = resolve_targets(args)
    print(f"[gate] targets: {', '.join(targets) or '(none)'}", flush=True)
    for name, reason in sorted(skipped.items()):
        print(f"[gate] skipping {name}: {reason}", flush=True)
    if not targets:
        print("[gate] nothing to install; no chart directory changed", flush=True)
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a") as handle:
                handle.write("## Chart install gate\n\nNo chart changed; nothing installed.\n"
                             + "".join(f"\n- skipped by name: `{name}` - {reason}"
                                       for name, reason in sorted(skipped.items())) + "\n")
        return 0

    gate = Gate(args)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    ok = False
    try:
        gate.setup(targets)
        for name in targets:
            gate.check(name)
        ok = gate.summary(targets, skipped)
    except (Exception, KeyboardInterrupt) as error:
        print(f"[gate] harness error: {type(error).__name__}: {gate.redact(str(error))}",
              flush=True)
        if gate.results:
            gate.summary(targets, skipped)
    finally:
        with gate.stage("Clean up owned resources"):
            gate.cleanup()
        print(f"[gate] {'PASSED' if ok else 'FAILED'}; artifacts: {gate.artifacts}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
