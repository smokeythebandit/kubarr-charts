# Shared NFS Subpath Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move NFS subpath preparation into one `kubarr-common` helper used by every consumer chart, and align Sonarr/Radarr/Jellyfin/Plex library paths with their location in the export.

**Architecture:** A single template helper renders an unprivileged init container that creates each writable, non-root `subPath` before the application starts, replacing seven copy-pasted copies and four root-running `mkdir && chown` containers. Library mounts then move to identical in-container and in-export paths, and Sonarr/Radarr register their own root folder idempotently.

**Tech Stack:** Helm 4 charts, Go templates (sprig), Python `unittest` chart tests, Rust CLI pins.

**Spec:** `docs/superpowers/specs/2026-10-27-shared-nfs-subpath-preparation-design.md`

## Global Constraints

- Chart revisions are immutable once published; every modified chart bumps its version exactly **once**, in Task 1.
- `kubarr-common` goes `0.4.0` → `0.5.0`. `tests/test_dependencies.py` requires every consumer's `Chart.yaml` pin **and** `Chart.lock` to equal that version.
- `system/kubarr-backend` and `system/kubarr-worker` keep `version` equal to `appVersion`; their images do not change, so they take build metadata `0.3.4+1`.
- Preparation containers never run as root, never request `CAP_CHOWN`, never allow privilege escalation.
- Preparation group is always gid `1000`. Default uid is `1000`; overrides are `65534` (victoriametrics) and `999` (postgresql, redmine).
- `tests/test_monitoring_startup.py` and `tests/test_system_database_startup.py` must pass **unmodified** throughout. They are the regression guard for the seven render-identical migrations.
- Busybox image pin: `busybox:1.37.0`.
- Run the full suite with `python3 -B -m unittest discover -s tests` from the repo root.

## File Structure

| File | Responsibility |
| --- | --- |
| `system/kubarr-common/templates/_storage.tpl` | New `prepareMounts` + `mediaGroup` helpers beside existing mount helpers |
| `system/kubarr-common/Chart.yaml` | Version `0.5.0` |
| 23 consumer `Chart.yaml` / `Chart.lock` | Dependency pin + own version bump |
| 18 consumer workload templates | Call the helper instead of inline/root-running preparation |
| `media-manager/{sonarr,radarr}/values.yaml` | Library mount → `media/shows` / `media/movies` |
| `media-server/{jellyfin,plex}/values.yaml` | Library mount target → `/media` |
| `media-manager/{sonarr,radarr}/templates/setup-configmap.yaml` | `ensure_root_folder()` |
| `tests/test_prepare_mounts.py` | New: helper contract across all consumers |
| `tests/test_arr_download_paths.py` | Extended: root-folder registration |
| `kubarr-cli/src/charts.rs`, `src/bootstrap.rs`, `CHANGELOG.md`, `Cargo.toml` | Bootstrap pins + release `0.2.10` |

---

### Task 1: Helper, common bump, and the 23 dependency pins

**Files:**
- Modify: `system/kubarr-common/templates/_storage.tpl`
- Modify: `system/kubarr-common/Chart.yaml`
- Modify: all 23 consumer `Chart.yaml` + `Chart.lock`
- Test: `tests/test_dependencies.py` (existing, must pass)

**Interfaces:**
- Consumes: nothing.
- Produces: `kubarr-common.storage.prepareMounts` taking `dict "root" . ["mounts" <list>] ["uid" <int>]` and rendering zero or one init container; `kubarr-common.storage.mediaGroup` taking `.` and rendering `supplementalGroups: [1000]`.

- [ ] **Step 1: Add both helpers to `_storage.tpl`**

Append after the `kubarr-common.storage.mediaClaimName` define:

```gotemplate
{{/* Kubelet creates subPath directories as root, which a normal export
     squashes. Create them first as the application's own uid in the media
     group, so no chown and no privileged step is involved. */}}
{{- define "kubarr-common.storage.prepareMounts" -}}
{{- $mounts := .root.Values.storage.mounts -}}
{{- if hasKey . "mounts" -}}{{- $mounts = .mounts -}}{{- end -}}
{{- $uid := default 1000 .uid -}}
{{- $selected := list -}}
{{- range $mount := $mounts -}}
{{- if and (eq $mount.mode "read-write") (ne $mount.source ".") -}}
{{- $selected = append $selected $mount -}}
{{- end -}}
{{- end -}}
{{- if $selected -}}
- name: prepare-media-directories
  image: {{ default "busybox:1.37.0" .root.Values.storage.prepareImage }}
  command: [sh, -ec, 'umask 0002; for directory do mkdir -p -- "$directory"; test -w "$directory"; done', --]
  args:
    {{- range $mount := $selected }}
    - {{ printf "/nfs/%s" $mount.source | quote }}
    {{- end }}
  securityContext:
    runAsUser: {{ $uid }}
    runAsGroup: 1000
    runAsNonRoot: true
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: [ALL]
  volumeMounts:
    - name: media
      mountPath: /nfs
{{- end -}}
{{- end }}

{{/* The shared export is writable by the media group. Applications whose uid
     is not 1000 need its membership to reach prepared directories. */}}
{{- define "kubarr-common.storage.mediaGroup" -}}
supplementalGroups: [1000]
{{- end }}
```

- [ ] **Step 2: Bump the common chart**

In `system/kubarr-common/Chart.yaml` set `version: 0.5.0`.

- [ ] **Step 3: Run the dependency test to verify it fails**

```bash
cd /home/benjamin/Projects/Kubarr/kubarr-charts
python3 -B -m unittest discover -s tests -p 'test_dependencies.py' -v
```

Expected: FAIL — consumers still pin `0.4.0` while common is `0.5.0`.

- [ ] **Step 4: Update every consumer pin and lock**

For each of the 23 consumers, set the `kubarr-common` dependency `version: 0.5.0` in `Chart.yaml`, then refresh the lock:

```bash
cd /home/benjamin/Projects/Kubarr/kubarr-charts
for chart in development/jenkins development/redmine development/scm-manager \
  download-client/deluge download-client/qbittorrent download-client/rutorrent \
  download-client/sabnzbd download-client/transmission indexer/jackett \
  media-manager/jellyseerr media-manager/radarr media-manager/sonarr \
  media-server/frigate media-server/immich media-server/jellyfin media-server/plex \
  monitoring/fluent-bit monitoring/headlamp monitoring/victorialogs \
  monitoring/victoriametrics system/kubarr-backend system/kubarr-worker \
  system/postgresql; do
  sed -i 's/^    version: 0\.4\.0$/    version: 0.5.0/' "$chart/Chart.yaml"
  helm dependency update "$chart" >/dev/null || echo "FAILED: $chart"
done
```

- [ ] **Step 5: Bump every consumer chart version once**

Increment the build metadata of each consumer's own `version` (e.g. `4.0.20+12` → `4.0.20+13`). For `system/kubarr-backend` and `system/kubarr-worker`, `0.3.4` → `0.3.4+1`, leaving `appVersion` at `0.3.4`. This is the only version bump in the whole plan; later tasks edit these charts without bumping again.

- [ ] **Step 6: Run the full suite**

```bash
python3 -B -m unittest discover -s tests
```

Expected: PASS. No chart calls the new helper yet, so every existing assertion still describes the current render.

- [ ] **Step 7: Commit**

```bash
git add system/kubarr-common '*/*/Chart.yaml' '*/*/Chart.lock'
git commit -m "feat(storage): add shared NFS subpath preparation helper"
```

---

### Task 2: Migrate the seven correct copies

**Files:**
- Modify: `download-client/qbittorrent/templates/deployment.yaml`
- Modify: `media-manager/sonarr/templates/deployment.yaml`
- Modify: `media-manager/radarr/templates/deployment.yaml`
- Modify: `media-server/jellyfin/templates/deployment.yaml`
- Modify: `monitoring/victorialogs/templates/deployment.yaml`
- Modify: `monitoring/victoriametrics/templates/deployment.yaml`
- Modify: `system/postgresql/templates/statefulset.yaml`
- Create: `tests/test_prepare_mounts.py`
- Test: `tests/test_monitoring_startup.py`, `tests/test_system_database_startup.py` (existing, unmodified)

**Interfaces:**
- Consumes: `kubarr-common.storage.prepareMounts` from Task 1.
- Produces: no new interface; establishes the call convention later tasks copy.

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_prepare_mounts.py`:

```python
"""Every writable NFS subpath is prepared by the shared unprivileged helper."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


# uid that must own each prepared directory: the identity the application writes as.
UIDS = {
    "development/jenkins": 1000,
    "development/redmine": 999,
    "development/scm-manager": 1000,
    "download-client/deluge": 1000,
    "download-client/qbittorrent": 1000,
    "download-client/rutorrent": 1000,
    "download-client/sabnzbd": 1000,
    "download-client/transmission": 1000,
    "media-manager/jellyseerr": 1000,
    "media-manager/radarr": 1000,
    "media-manager/sonarr": 1000,
    "media-server/frigate": 1000,
    "media-server/immich": 1000,
    "media-server/jellyfin": 1000,
    "media-server/plex": 1000,
    "monitoring/victorialogs": 1000,
    "monitoring/victoriametrics": 65534,
    "system/postgresql": 999,
}
# Consumers with no writable subPath to prepare: no init container may appear.
WITHOUT_PREPARATION = ("indexer/jackett", "monitoring/headlamp", "system/kubarr-backend")


def workloads(chart):
    path = ROOT / chart
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", path.name, str(chart_input(path))],
        check=True, capture_output=True, text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout)
            if doc and doc.get("kind") in ("Deployment", "StatefulSet", "DaemonSet")]


def prepared_sources(pod):
    """Writable media subPaths the application containers actually mount."""
    sources = set()
    for container in pod.get("containers", []) + pod.get("initContainers", []):
        if container["name"] == "prepare-media-directories":
            continue
        for mount in container.get("volumeMounts", []):
            if mount["name"] == "media" and not mount.get("readOnly") and "subPath" in mount:
                sources.add(mount["subPath"])
    return sources


class PrepareMountsTests(unittest.TestCase):
    def test_every_writable_subpath_is_prepared_exactly_once(self):
        for chart, uid in UIDS.items():
            for workload in workloads(chart):
                pod = workload["spec"]["template"]["spec"]
                sources = prepared_sources(pod)
                name = workload["metadata"]["name"]
                with self.subTest(chart=chart, workload=name):
                    prepare = [c for c in pod.get("initContainers", [])
                               if c["name"] == "prepare-media-directories"]
                    if not sources:
                        self.assertEqual(prepare, [])
                        continue
                    self.assertEqual(len(prepare), 1)
                    args = prepare[0]["args"]
                    self.assertEqual(sorted(args), sorted(f"/nfs/{s}" for s in sources))
                    self.assertEqual(len(args), len(set(args)))
                    self.assertEqual(prepare[0]["volumeMounts"],
                                     [{"name": "media", "mountPath": "/nfs"}])
                    context = prepare[0]["securityContext"]
                    self.assertEqual(context["runAsUser"], uid)
                    self.assertEqual(context["runAsGroup"], 1000)
                    self.assertTrue(context["runAsNonRoot"])
                    self.assertFalse(context["allowPrivilegeEscalation"])
                    self.assertEqual(context["capabilities"], {"drop": ["ALL"]})

    def test_no_preparation_step_relies_on_root_or_chown(self):
        for chart in list(UIDS) + list(WITHOUT_PREPARATION):
            for workload in workloads(chart):
                pod = workload["spec"]["template"]["spec"]
                for container in pod.get("initContainers", []):
                    with self.subTest(chart=chart, container=container["name"]):
                        context = container.get("securityContext", {})
                        self.assertNotEqual(context.get("runAsUser"), 0)
                        self.assertNotIn("CHOWN", context.get("capabilities", {}).get("add", []))
                        self.assertNotIn("chown", " ".join(container.get("command", [])))

    def test_consumers_without_writable_subpaths_prepare_nothing(self):
        for chart in WITHOUT_PREPARATION:
            for workload in workloads(chart):
                pod = workload["spec"]["template"]["spec"]
                with self.subTest(chart=chart):
                    self.assertEqual(
                        [c for c in pod.get("initContainers", [])
                         if c["name"] == "prepare-media-directories"], [])

    def test_rendered_command_is_idempotent_and_group_writable(self):
        pod = workloads("system/postgresql")[0]["spec"]["template"]["spec"]
        prepare, = [c for c in pod["initContainers"]
                    if c["name"] == "prepare-media-directories"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "system" / "postgresql"
            for _ in range(2):
                subprocess.run([*prepare["command"], str(target)],
                               check=True, capture_output=True, text=True)
            self.assertTrue(target.is_dir())
            self.assertEqual(target.stat().st_mode & 0o777, 0o775)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python3 -B -m unittest discover -s tests -p 'test_prepare_mounts.py' -v
```

Expected: FAIL — charts in `UIDS` that currently have no preparation (deluge, plex, frigate, …) report `len(prepare) == 0`, and jenkins/redmine/scm-manager/immich fail the root/chown assertions.

- [ ] **Step 3: Replace the inline block in each of the seven charts**

In each file, delete the inline `initContainers:` block containing `prepare-media-directories` **and** the `{{- $writableMounts := ... }}` preamble at the top of the file, then insert the helper call where the block was. Keep each chart's existing `supplementalGroups` line, if any, as is.

`system/postgresql/templates/statefulset.yaml`, in place of the removed block:

```gotemplate
      {{- include "kubarr-common.storage.prepareMounts" (dict "root" . "uid" 999) | nindent 6 }}
```

Guard it so an empty render does not leave a stray `initContainers:` key — wrap with the same emptiness test the helper uses by including it into a variable:

```gotemplate
      {{- $prepare := include "kubarr-common.storage.prepareMounts" (dict "root" . "uid" 999) }}
      {{- if $prepare }}
      initContainers:
        {{- $prepare | nindent 8 }}
      {{- end }}
```

Use the same shape in the other six, changing only `uid`: `1000` for qbittorrent, sonarr, radarr, jellyfin and victorialogs; `65534` for victoriametrics. Omit the `"uid"` key entirely where it is 1000.

- [ ] **Step 4: Verify the render did not change**

```bash
python3 -B -m unittest discover -s tests -p 'test_monitoring_startup.py' -v
python3 -B -m unittest discover -s tests -p 'test_system_database_startup.py' -v
```

Expected: PASS, with both files unmodified. This is the proof the migration is render-identical.

- [ ] **Step 5: Commit**

```bash
git add system/kubarr-common tests/test_prepare_mounts.py \
  download-client/qbittorrent media-manager/sonarr media-manager/radarr \
  media-server/jellyfin monitoring/victorialogs monitoring/victoriametrics \
  system/postgresql
git commit -m "refactor(storage): render existing subpath preparation from the shared helper"
```

---

### Task 3: Replace the four root-running preparation containers

**Files:**
- Modify: `development/jenkins/templates/statefulset.yaml`
- Modify: `development/scm-manager/templates/statefulset.yaml`
- Modify: `development/redmine/templates/deployment.yaml`
- Modify: `development/redmine/templates/postgres.yaml`
- Modify: `media-server/immich/templates/ml.yaml`
- Test: `tests/test_prepare_mounts.py`, `tests/test_declarative_storage.py`

**Interfaces:**
- Consumes: `kubarr-common.storage.prepareMounts`, `kubarr-common.storage.mediaGroup`.
- Produces: nothing new.

These five workloads currently run `sh -ec 'mkdir -p -- "$1" && chown <uid>:<uid> -- "$1"'` as `runAsUser: 0` with `capabilities.add: [CHOWN]`. Root squashing defeats both the mkdir and the chown, so they are broken on a normal export.

- [ ] **Step 1: Confirm the failing assertions**

```bash
python3 -B -m unittest discover -s tests -p 'test_prepare_mounts.py' \
  -k test_no_preparation_step_relies_on_root_or_chown -v
```

Expected: FAIL naming `prepare-home`, `prepare-files`, `prepare-database`, `prepare-model-cache`.

- [ ] **Step 2: Replace each block**

Delete the whole `initContainers` entry for `prepare-home` / `prepare-files` / `prepare-database` / `prepare-model-cache` and insert, with the uid that container used to chown to:

```gotemplate
      {{- $prepare := include "kubarr-common.storage.prepareMounts" (dict "root" . "uid" 1000) }}
      {{- if $prepare }}
      initContainers:
        {{- $prepare | nindent 8 }}
      {{- end }}
```

uid per workload: jenkins `1000`, scm-manager `1000`, redmine `999`, redmine postgres `999`, immich-ml `1000`.

Redmine's postgres workload and immich's ML workload pass their own mount lists, so they must pass them through:

```gotemplate
      {{- $prepare := include "kubarr-common.storage.prepareMounts" (dict "root" . "uid" 999 "mounts" .Values.storage.workloads.postgres.mounts) }}
```

and for immich ML:

```gotemplate
      {{- $prepare := include "kubarr-common.storage.prepareMounts" (dict "root" . "uid" 1000 "mounts" .Values.storage.workloads.machineLearning.mounts) }}
```

Redmine's workloads run as uid 999, which is not 1000, so add the media group to both pod security contexts:

```gotemplate
        {{- include "kubarr-common.storage.mediaGroup" . | nindent 8 }}
```

Note: redmine also keeps its separate `wait-for-postgres` init container, which is unrelated and stays.

- [ ] **Step 3: Run the affected tests**

```bash
python3 -B -m unittest discover -s tests -p 'test_prepare_mounts.py' -v
python3 -B -m unittest discover -s tests -p 'test_declarative_storage.py' -v
```

Expected: the root/chown test now passes; `test_prepare_home_uses_matching_target_not_first_list_entry` in `test_declarative_storage.py` still passes because the helper selects by mount, not by list position. If that test asserts the old container name, update the name it looks for — that is a renamed step, not a behaviour change.

- [ ] **Step 4: Commit**

```bash
git add development/jenkins development/scm-manager development/redmine media-server/immich tests
git commit -m "fix(storage): prepare development and immich subpaths without root"
```

---

### Task 4: Add preparation to the eight remaining workloads

**Files:**
- Modify: `download-client/deluge/templates/deployment.yaml`
- Modify: `download-client/rutorrent/templates/deployment.yaml`
- Modify: `download-client/sabnzbd/templates/deployment.yaml`
- Modify: `download-client/transmission/templates/deployment.yaml`
- Modify: `media-manager/jellyseerr/templates/deployment.yaml`
- Modify: `media-server/frigate/templates/deployment.yaml`
- Modify: `media-server/immich/templates/deployment.yaml`
- Modify: `media-server/plex/templates/deployment.yaml`
- Test: `tests/test_prepare_mounts.py`

**Interfaces:**
- Consumes: `kubarr-common.storage.prepareMounts`.
- Produces: nothing new.

- [ ] **Step 1: Confirm which charts still fail**

```bash
python3 -B -m unittest discover -s tests -p 'test_prepare_mounts.py' \
  -k test_every_writable_subpath_is_prepared_exactly_once -v
```

Expected: FAIL for deluge, rutorrent, sabnzbd, transmission, jellyseerr, frigate, immich (main) and plex.

- [ ] **Step 2: Insert the helper call in each**

In each deployment's pod spec, immediately before `containers:`, all at the default uid so no `"uid"` key is needed:

```gotemplate
      {{- $prepare := include "kubarr-common.storage.prepareMounts" (dict "root" .) }}
      {{- if $prepare }}
      initContainers:
        {{- $prepare | nindent 8 }}
      {{- end }}
```

Each of these charts has exactly one writable media mount, so each renders a single argument: `/nfs/downloads/deluge`, `/nfs/downloads/rutorrent`, `/nfs/downloads/sabnzbd`, `/nfs/downloads/transmission`, `/nfs/media`, `/nfs/frigate`, `/nfs/immich`, `/nfs/transcode/plex`.

- [ ] **Step 3: Run the test to verify it passes**

```bash
python3 -B -m unittest discover -s tests -p 'test_prepare_mounts.py' -v
```

Expected: PASS for all four tests.

- [ ] **Step 4: Run the full suite**

```bash
python3 -B -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add download-client media-manager/jellyseerr media-server
git commit -m "fix(storage): prepare download-client and media-server subpaths"
```

---

### Task 5: Library path defaults

**Files:**
- Modify: `media-manager/sonarr/values.yaml:97`
- Modify: `media-manager/radarr/values.yaml:97`
- Modify: `media-server/jellyfin/values.yaml:113`
- Modify: `media-server/plex/values.yaml:119`
- Test: `tests/test_prepare_mounts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: library mount targets `/media/shows`, `/media/movies`, `/media` that Task 6 reads for root-folder registration.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prepare_mounts.py`:

```python
LIBRARY_MOUNTS = {
    "media-manager/sonarr": ("media/shows", "/media/shows", "read-write"),
    "media-manager/radarr": ("media/movies", "/media/movies", "read-write"),
    "media-server/jellyfin": ("media", "/media", "read"),
    "media-server/plex": ("media", "/media", "read"),
}


class LibraryPathTests(unittest.TestCase):
    def test_library_mounts_use_identical_export_and_container_paths(self):
        for chart, (source, target, mode) in LIBRARY_MOUNTS.items():
            with self.subTest(chart=chart):
                mounts = yaml.safe_load((ROOT / chart / "values.yaml").read_text())["storage"]["mounts"]
                library = next(m for m in mounts if m["name"] == "library")
                self.assertEqual(library["source"], source)
                self.assertEqual(library["target"], target)
                self.assertEqual(library["mode"], mode)
                self.assertEqual(library["target"], "/" + library["source"])
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python3 -B -m unittest discover -s tests -p 'test_prepare_mounts.py' \
  -k test_library_mounts_use_identical_export_and_container_paths -v
```

Expected: FAIL — sonarr's source is `media` and jellyfin's target is `/data`.

- [ ] **Step 3: Change the four mounts**

```yaml
# media-manager/sonarr/values.yaml
    - {name: library, source: media/shows, target: /media/shows, mode: read-write}
# media-manager/radarr/values.yaml
    - {name: library, source: media/movies, target: /media/movies, mode: read-write}
# media-server/jellyfin/values.yaml
    - {name: library, source: media, target: /media, mode: read}
# media-server/plex/values.yaml
    - {name: library, source: media, target: /media, mode: read}
```

- [ ] **Step 4: Run the full suite**

```bash
python3 -B -m unittest discover -s tests
```

Expected: PASS. Sonarr and Radarr now prepare `/nfs/media/shows` and `/nfs/media/movies` instead of `/nfs/media`, which `test_prepare_mounts.py` derives from the mounts and so follows automatically.

- [ ] **Step 5: Commit**

```bash
git add media-manager/sonarr/values.yaml media-manager/radarr/values.yaml \
  media-server/jellyfin/values.yaml media-server/plex/values.yaml tests/test_prepare_mounts.py
git commit -m "feat(storage): give each library manager its own matching media path"
```

---

### Task 6: Root-folder registration

**Files:**
- Modify: `media-manager/sonarr/templates/setup-configmap.yaml`
- Modify: `media-manager/radarr/templates/setup-configmap.yaml`
- Test: `tests/test_arr_download_paths.py`

**Interfaces:**
- Consumes: the `library` mount target from Task 5.
- Produces: shell function `ensure_root_folder <api_key>` in both rendered setup scripts, returning 0 when the root folder is present or created.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_arr_download_paths.py`, reusing the existing stub-server pattern:

```python
ROOT_FOLDERS = {"sonarr": "/media/shows", "radarr": "/media/movies"}


class RootFolderTests(unittest.TestCase):
    def exercise(self, app, existing=None, get_status=200, post_status=201, malformed=False):
        state = {"folders": list(existing or []), "posts": [], "requests": []}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append(("GET", self.path))
                self.send_response(get_status)
                self.end_headers()
                self.wfile.write(b"not-json" if malformed else json.dumps(state["folders"]).encode())

            def do_POST(self):
                state["requests"].append(("POST", self.path))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["posts"].append(body)
                self.send_response(post_status)
                self.end_headers()
                if post_status < 400:
                    state["folders"].append({"id": 1, **body})
                self.wfile.write(b"{}")

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        script = setup_script(app)
        functions = script.split('echo "[setup] Starting download client manager for ')[0]
        command = functions + f'''
API_PORT={server.server_port}
ensure_root_folder 'secret-api-key'
first=$?
ensure_root_folder 'secret-api-key'
second=$?
[ "$first" -eq 0 ] && [ "$second" -eq 0 ]
'''
        try:
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=10)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertNotIn("secret-api-key", result.stdout + result.stderr)
        self.assertTrue(all(path == f"/{app}/api/v3/rootfolder" for _, path in state["requests"]))
        return result, state

    def test_registers_the_library_root_folder_once(self):
        for app, path in ROOT_FOLDERS.items():
            with self.subTest(app=app):
                result, state = self.exercise(app)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["posts"], [{"path": path}])

    def test_existing_root_folder_is_never_replaced(self):
        for app, path in ROOT_FOLDERS.items():
            for stored in (path, path + "/"):
                with self.subTest(app=app, stored=stored):
                    existing = [{"id": 7, "path": stored}]
                    result, state = self.exercise(app, existing)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(state["posts"], [])

    def test_unrelated_root_folder_does_not_hide_a_missing_one(self):
        for app, path in ROOT_FOLDERS.items():
            with self.subTest(app=app):
                result, state = self.exercise(app, [{"id": 7, "path": "/elsewhere"}])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["posts"], [{"path": path}])

    def test_failed_or_malformed_read_never_registers(self):
        for app in ROOT_FOLDERS:
            for options in ({"get_status": 503}, {"malformed": True}):
                with self.subTest(app=app, options=options):
                    result, state = self.exercise(app, **options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(state["posts"], [])

    def test_failed_write_returns_failure_for_next_pass_to_retry(self):
        for app in ROOT_FOLDERS:
            with self.subTest(app=app):
                result, state = self.exercise(app, post_status=503)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state["posts"]), 2)
                self.assertEqual(state["folders"], [])
```

- [ ] **Step 2: Run them to verify they fail**

```bash
python3 -B -m unittest discover -s tests -p 'test_arr_download_paths.py' -v
```

Expected: FAIL — `ensure_root_folder: command not found`.

- [ ] **Step 3: Add the function to both setup scripts**

In `media-manager/sonarr/templates/setup-configmap.yaml` and the Radarr equivalent, directly after `ensure_qbittorrent_path_mapping()`:

```gotemplate
{{- $library := "" }}
{{- range .Values.storage.mounts }}{{- if eq .name "library" }}{{- $library = .target }}{{- end }}{{- end }}
{{- if $library }}
    ensure_root_folder() {
      local api_key="$1"
      local endpoint="http://localhost:$API_PORT$API_BASE/api/v3/rootfolder"
      local wanted={{ $library | quote }}
      local folders

      # The library mount is this app's own subtree. Register it once so the
      # application is usable without manual configuration, and never replace
      # a path an operator chose.
      folders=$(curl -fsS --connect-timeout 3 --max-time 10 \
        -H "X-Api-Key: $api_key" "$endpoint") || return 1
      echo "$folders" | jq -e 'type == "array"' > /dev/null || return 1
      if echo "$folders" | jq -e --arg wanted "$wanted" '
        any(.[]; (.path // "" | rtrimstr("/")) == $wanted)
      ' > /dev/null; then
        return 0
      fi

      curl -fsS --connect-timeout 3 --max-time 10 -X POST \
        -H "X-Api-Key: $api_key" -H "Content-Type: application/json" \
        "$endpoint" -d "$(jq -nc --arg path "$wanted" '{path: $path}')" > /dev/null || return 1
      echo "[setup] Registered root folder $wanted"
    }
{{- end }}
```

- [ ] **Step 4: Call it once per reconciliation pass**

In the main loop of both scripts, immediately after the `is_api_ready` guard and before the client loop:

```gotemplate
{{- if $library }}
      if ! ensure_root_folder "$API_KEY"; then
        echo "[setup] Could not register the root folder; retrying next pass"
      fi
{{- end }}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python3 -B -m unittest discover -s tests -p 'test_arr_download_paths.py' -v
```

Expected: PASS, including the pre-existing remote-path-mapping tests.

- [ ] **Step 6: Run the full suite and lint every modified chart**

```bash
python3 -B -m unittest discover -s tests
for chart in $(git diff --name-only HEAD~5 | grep -o '^[a-z-]*/[a-z-]*' | sort -u); do
  helm lint "$chart" >/dev/null || echo "LINT FAILED: $chart"
done
```

Expected: suite PASS, no lint failures.

- [ ] **Step 7: Commit**

```bash
git add media-manager/sonarr media-manager/radarr tests/test_arr_download_paths.py
git commit -m "feat(media-manager): register each library root folder idempotently"
```

---

### Task 7: Publish the charts

**Files:**
- No source changes. Push triggers `.github/workflows/publish.yml`.

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: published chart revisions that Task 8 pins.

- [ ] **Step 1: Verify the tree is green and the versions are consistent**

```bash
python3 -B -m unittest discover -s tests
git log --oneline origin/main..HEAD
```

Expected: suite PASS, six commits from Tasks 1-6.

- [ ] **Step 2: Push to main**

```bash
git push origin main
```

- [ ] **Step 3: Watch the publish workflow to completion**

```bash
gh run list --repo smokeythebandit/kubarr-charts --limit 1
gh run view <id> --repo smokeythebandit/kubarr-charts --json status,conclusion
```

Expected: `completed success`.

- [ ] **Step 4: Verify the six bootstrap revisions are pullable with the fix**

```bash
cd /tmp && rm -rf pubcheck && mkdir pubcheck && cd pubcheck
for entry in "postgresql:16.15.0+10" "fluent-bit:5.1.5+11" "victorialogs:1.53.0+10" \
             "victoriametrics:1.154.1+12" "kubarr-worker:0.3.4+1" "kubarr-backend:0.3.4+1"; do
  name="${entry%%:*}"; version="${entry#*:}"
  helm pull "oci://ghcr.io/smokeythebandit/kubarr-charts/$name" --version "$version" >/dev/null \
    && echo "OK   $name $version" || echo "FAIL $name $version"
done
```

Replace each version with the value actually set in Task 1 Step 5. Expected: six `OK` lines.

---

### Task 8: Pin the new charts and release the CLI

**Files:**
- Modify: `/home/benjamin/Projects/Kubarr/kubarr-cli/src/charts.rs:8-20`
- Modify: `/home/benjamin/Projects/Kubarr/kubarr-cli/src/bootstrap.rs:121-131`
- Modify: `/home/benjamin/Projects/Kubarr/kubarr-cli/CHANGELOG.md`
- Modify: `/home/benjamin/Projects/Kubarr/kubarr-cli/Cargo.toml`, `Cargo.lock`
- Test: `cargo test --locked`, `tests/installer/run.py`

**Interfaces:**
- Consumes: the published revisions from Task 7.
- Produces: CLI `0.2.10` installing the prepared charts.

- [ ] **Step 1: Update the pins in both files**

In `src/charts.rs`, set the new versions for `postgresql`, `fluent-bit`, `victorialogs`, `victoriametrics`, `kubarr-backend` and `kubarr-worker`. Apply the identical values to the expected-pin table in `src/bootstrap.rs`. `managed-nfs`, `grafana`, `kubarr-frontend` and `openresty` are unchanged.

- [ ] **Step 2: Run the Rust suite to verify the tables agree**

```bash
cd /home/benjamin/Projects/Kubarr/kubarr-cli
cargo test --locked
```

Expected: PASS, 95 tests. A mismatch between `charts.rs` and `bootstrap.rs` fails here.

- [ ] **Step 3: Bump the CLI version and write the changelog**

Set `version = "0.2.10"` in `Cargo.toml`, run `cargo update -p kubarr-cli --precise 0.2.10`, and add a `## 0.2.10` section to `CHANGELOG.md` recording: shared unprivileged subpath preparation across all charts, the removal of root-running preparation steps, Sonarr/Radarr owning `/media/shows` and `/media/movies`, Jellyfin and Plex reading the library at `/media`, and automatic root-folder registration.

- [ ] **Step 4: Run the published-mode acceptance journey**

```bash
cd /home/benjamin/Projects/Kubarr/kubarr-cli
cargo build --locked
python3 tests/installer/run.py
```

Expected: `[e2e] PASSED`. This exercises a migrated bootstrap chart (postgresql, both Victoria components, kubarr-worker) and a migrated application chart (Sonarr) against the real registry.

- [ ] **Step 5: Commit, push, and tag**

```bash
git add -A
git commit -m "fix(installer): release v0.2.10 with shared NFS subpath preparation"
git push origin main
```

Wait for the CLI CI run to pass, including installer acceptance, then:

```bash
git tag -a v0.2.10 -m "Kubarr CLI v0.2.10"
git push origin v0.2.10
```

- [ ] **Step 6: Verify the published release**

```bash
gh release list --repo smokeythebandit/kubarr-cli --limit 2
```

Expected: `Kubarr CLI v0.2.10` marked `Latest`.

---

## Self-Review

**Spec coverage:** helper (Task 1), seven migrations (Task 2), four root-running replacements (Task 3), eight additions (Task 4), library paths (Task 5), root folder (Task 6), versioning and the 23 pins (Task 1), publish ordering (Tasks 7-8), test strategy (Tasks 2, 5, 6), acceptance run (Task 8). `system/kubarr-backend` is covered by Task 1's pin bump and asserted to render no preparation container by `WITHOUT_PREPARATION`.

**Placeholders:** none. Chart versions in Task 7 Step 4 are explicitly "replace with the value set in Task 1 Step 5" because the exact build-metadata numbers are chosen during that step.

**Type consistency:** `prepareMounts` takes `root`/`mounts`/`uid` and is called with exactly those keys in Tasks 2, 3 and 4. `mediaGroup` takes `.` and is used only in Task 3. `ensure_root_folder` takes one argument in both its definition and its call site. The test helper `prepared_sources` is defined once in Task 2 and reused by the assertions added in Task 5.

**Known uncertainty:** `media-server/frigate` and `media-server/immich` do not declare `runAsUser` in values, so uid 1000 is chosen to match the group-writable export convention rather than a verified application identity. If either application writes as a different uid, its prepared directory will be owned by 1000 with group write, which the media group still permits.
