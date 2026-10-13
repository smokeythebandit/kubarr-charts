# Kubarr Charts

Helm charts for Kubarr and the applications it manages. Charts are grouped under
`development/`, `download-client/`, `indexer/`, `media-manager/`,
`media-server/`, `monitoring/`, and `system/`. There is no umbrella `kubarr` chart; use the Kubarr CLI to bootstrap
the platform and its storage, database, monitoring, gateway, and worker services.

## Local Validation

Use Helm 4.3.0 (the CI version), LuaJIT, Python 3, and PyYAML 6.0.3. Install Python
dependencies in a virtual environment if your operating system requires one.

```sh
python3 -m pip install PyYAML==6.0.3
helm repo add jetstack https://charts.jetstack.io
for chart in */*/Chart.yaml; do
  chart_dir="${chart%/Chart.yaml}"
  helm dependency build --skip-refresh "$chart_dir"
  helm lint "$chart_dir"
done
python3 -B -m unittest discover -s tests -v
```

The regression tests render every shared NetworkPolicy consumer with default
names, custom release names, name/fullname overrides, distinct service and pod
ports, and disabled policies. They verify selectors against the app's actual pod
labels and ensure ingress permits its HTTP container port. They do not verify CNI
enforcement or runtime connectivity; those require a cluster with NetworkPolicy
support.

Gateway tests execute the rendered Lua routing block with mocked OpenResty APIs.
They cover frontend 404 fallback, app status redirects, and routing without
bypassing authorization or masking unexpected lookup failures. Live proxy behavior
still requires integration testing.

CI runs these behavioral checks against packaged charts. To reproduce that mode,
package every chart into an empty directory and set `CHART_PACKAGES_DIR` to its
absolute path when running the test command. Missing or extra `.tgz` files fail
validation; tests never fall back to source when package mode is selected.
Package checks verify chart metadata and render all application archives against
Kubernetes 1.35.8. This verifies packaging and YAML structure, not Kubernetes schema
admission. Without the variable, behavioral tests use source charts and the package
test creates temporary archives automatically.

## Dependencies And Releases

Dependabot owns automated chart and GitHub Actions version PRs; Renovate is
disabled in `renovate.json` to avoid duplicate PRs (its former rules remain for
reference). Once `.github/dependabot.yml` is merged into the repository's
default branch and Dependabot version updates are enabled, it checks chart
`Chart.yaml` dependencies and image references in `values.yaml` across all
seven chart groups every Monday at 09:00 UTC, and checks GitHub Actions on the
same schedule. Version PRs are limited to five open Helm PRs and two Actions
PRs; Actions updates are grouped. No updates are auto-merged. Dependabot
security updates, where supported and enabled on GitHub, are separate from the
weekly version schedule; GitHub does **not** support Dependabot security
updates for Helm charts. These checks do not cover literal images in templates,
and the local `kubarr-common` dependency and Kubarr-built images are excluded
and must be updated manually.

Review each chart update PR before merging: for a primary app image, adjust
`appVersion` and the chart version's normalized app-version core; for a sidecar,
bump the chart's `+revision` without changing the primary app version. Update
locks where appropriate and run the chart tests. Dependabot's Helm support is
documented for Helm 3; this repository validates with Helm 4.3, so confirm
generated changes build and render correctly under Helm 4 before merging.

`system/kubarr-common` supplies shared Helm helpers. When changing it, bump its
version and each affected consumer's dependency and chart version, then run
`helm dependency update --skip-refresh <chart-directory>`. Include the resulting
`Chart.lock` changes. Dependency archives under `charts/` are generated for local
validation and publishing, ignored by Git, and must not be committed.

Pull requests run lint, packaging, and regression tests. Main-branch pushes run the same
validation before publishing OCI charts to `ghcr.io/<repository-owner>/kubarr-charts`.
Validation uploads a `validated-charts` artifact. Publishing downloads and pushes
those exact archives, without checking out or rebuilding chart sources.
Existing versions are skipped, so every published chart change requires a chart
version bump. Application chart versions use the normalized `appVersion` as their
core and build metadata as the chart revision.

## Declarative storage (breaking upgrade)

**Current chart values and upgrade procedure:** [STORAGE-MIGRATION.md](STORAGE-MIGRATION.md).
Existing installations must verify and migrate application config to its
private claim before upgrading. Removed keys fail rendering on `--reuse-values`;
use reviewed new values and `--reset-values`. Chart upgrades do not copy data.

## Historical storage notes (pre-declarative releases only)

The following notes describe releases **before** the breaking declarative
storage change. Their `layout`, `profile`, `access` and path override examples
are not accepted by the current charts; use the migration document above.

### Shared media and private application config

Radarr, Sonarr, Jellyseerr, Plex, Jellyfin, Jackett, Deluge, qBittorrent,
Transmission, ruTorrent and SABnzbd accept `storage.layout: legacy|split`.
The default **legacy** mode preserves existing `media-data` PV/PVC names,
subpaths and pod mounts: `/config` (Jellyseerr: `/app/config`) still lives in
`storage.media.configSubPath` on the shared managed-NFS export. Existing
installations need no storage migration for a default chart upgrade. The
`storage.media` settings, including `create`, `existingClaim`, and `nfs`,
continue to control the shared media claim in both modes. When selecting an
already provisioned media claim, set `storage.media.create=false`; otherwise
the helper still renders its managed/external NFS PV/PVC. The managed-NFS
server and its backing storage are unchanged.

Opt-in **split** mode uses a *separate, private* `ReadWriteOnce` config PVC
named `<chart-fullname>-config`. Its root is mounted at the application's
existing config mount path (no `subPath`). `storage.config.create=true` creates
the PVC with `storage.config.size` (default `5Gi`); a nonempty
`storage.config.storageClassName` chooses a dedicated class. An empty class
uses the **cluster default StorageClass**, not the managed NFS export: check
that this class meets your app's storage needs. To mount a pre-provisioned
claim instead, set `storage.config.create=false` and
`storage.config.existingClaim=<name>` in the application's namespace; no
config PVC is rendered. The selected config claim must differ from
`storage.media.existingClaim`. No NFS-specific config backend is provided by
split mode; a suitable externally provisioned claim can be selected if desired.
New chart-created config PVCs have `helm.sh/resource-policy: keep` so Helm
does not delete private state during a rollback to legacy or uninstall.
Retained claims still require explicit cleanup after a verified backup, and
reuse/reinstall requires checking the existing claim's ownership and contents.

| Charts | Legacy source on `media-data` | Split destination |
| --- | --- | --- |
| Radarr, Sonarr, Jellyseerr, Plex, Jellyfin, Jackett | `config/<chart-name>` | Root of config claim |
| Deluge, qBittorrent, Transmission, ruTorrent, SABnzbd | `config/<chart-name>` | Root of config claim |

The source path is relative to the media claim's export root; use your actual
`storage.media.configSubPath` if overridden. Jellyseerr's destination mount
is `/app/config`; all others use `/config` by default. Shared libraries,
downloads, Plex transcode data, and Jellyfin cache remain at their *existing*
media subpaths. Radarr/Sonarr exporter pods and config-seeding init containers
use the same selected config claim. In split mode those exporters have required
pod affinity to the app pod so the default RWO claim mounts on the same node;
an unavailable app node also prevents the exporter from scheduling. Split
mode requires one app replica and uses a Recreate app rollout to prevent
concurrent writers; expect downtime during upgrades.

**For an installed release switching to split:** stop the app and any exporter
or init writer; back up the old config subdirectory and, separately, its
media data. Pre-provision a *new* destination PVC in the same namespace and
select it with `storage.config.create=false` and
`storage.config.existingClaim=<new-claim>`; do not let Helm create a second PVC
over the claim used for the copy. Keep the pre-provisioned claim under your own
retention/backup policy. Then
copy the *contents* of the legacy config subdirectory—including hidden files,
database sidecars and ownership/permissions—to the **root** of the new claim
using a controlled job/pod with access to both claims. Verify the copied data
and access by the application's UID/GID before enabling the new mounts. Then
upgrade with `storage.layout=split` and `storage.migrationConfirmed=true`;
verify the app and exporter before resuming writers. Keep the old media config
directory and independent backup until restore has been tested. To roll back,
stop writers and reconcile any newer changes back into the old directory before
re-enabling legacy mode. Do not assume a Helm rollback copies data. For an
already split installation, continue passing `migrationConfirmed=true` on
subsequent split-mode upgrades after confirming the destination claim contains
the expected data. Fresh split installs do not require the acknowledgement.
Helm templates and offline `helm template --is-upgrade` cannot inspect or prove
that data was copied; the acknowledgement is an operator assertion, not an
automatic migration. A wrong claim name or empty claim can still cause the
app to bootstrap fresh state; check PVCs, namespace, and mounts before upgrade.

### Declarative NFS data mounts

The same 11 charts accept `storage.mounts` to select NFS directories, container
paths, and access modes for the **main application container**. Each entry has
four required fields: `name` (a unique DNS-label identifier), `source` (a safe
path relative to the root of `storage.media.existingClaim`'s NFS export, with
no leading `/`), `target` (an absolute path inside the container), and `mode`
(`read` or `read-write` exactly). `read` sets Kubernetes `readOnly: true`;
`read-write` leaves the mount writable. All entries use the existing shared
media PVC; `name` identifies the entry, **not** a separate volume or claim.
The referenced NFS subdirectories must exist and have suitable server-side
permissions. For example, with the default managed export:

```yaml
# Values for a fresh Plex installation (on upgrades, first migrate config).
storage:
  layout: split
  mounts:
    - name: movies
      source: media/movies
      target: /data/movies
      mode: read
    - name: tv
      source: media/tv
      target: /data/tv
      mode: read
    - name: transcode
      source: transcode/plex
      target: /transcode
      mode: read-write
```

For qBittorrent, use `source: downloads/qbittorrent`, `target: /downloads`,
`mode: read-write`. A shared-profile Radarr can list both
`source: media`, `target: /media`, `mode: read-write` and
`source: downloads/qbittorrent`, `target: /downloads/qbittorrent`,
`mode: read-write`; other clients must be listed too if its existing setup
script or imports use them. In restricted profile, selected Radarr/Sonarr
download clients **must** retain their exact writable
`downloads/<client>` → `/downloads/<client>` mounts to match the setup script.
Restricted mounts may only select directories under the chart's role paths:
selected client directories and library for managers, own download directory
for clients, library plus own transcode/cache for Plex/Jellyfin; Jackett and
Jellyseerr have no allowed restricted data source. A restricted Plex/Jellyfin
library must be mounted `read` when `storage.readOnly=true`.

`mounts: null` (the default) or an omitted key preserves every existing
role/profile data mount. An explicit list **replaces** those mounts, including
the usual Plex transcode/Jellyfin cache and manager download mounts; it does
not append to them. `mounts: []` removes all shared-NFS data mounts from the
main app container. Explicit lists require `storage.layout: split`: config
stays on its private PVC at `/config` (Jellyseerr: `/app/config`) and cannot be
overridden by this list. Config-seeding init containers and exporter pods
retain only their existing config mounts; data mounts are never added to them
or to VPN and metrics sidecars. The media PV/PVC is retained even if the list
is empty, to avoid deleting Helm-managed storage on upgrade. Validation rejects
unsafe/overlapping target paths (including config and setup-script mounts),
config-root sources, overlapping sources with conflicting modes, unknown
fields, and invalid or duplicate names. Changing mounts does not move data or
rewrite application paths and remote path mappings. Check the split-config
migration procedure above before upgrading an installed release. These mounts
control the container filesystem view; they are not NFS-side authorization
(see **Boundary** below).

### Restricted application storage mounts

The 11 charts above also accept `storage.profile: restricted` (default:
`shared`). Restricted requires `storage.layout: split` so config is on its
private claim, **after** the migration described above for existing releases.
For Radarr and Sonarr, explicitly select each download client's existing
directory that the manager imports from. For example, in Radarr values:

```yaml
storage:
  layout: split
  migrationConfirmed: true # upgrades only, after verifying the config copy
  profile: restricted
  access:
    downloadClients: [qbittorrent, sabnzbd]
```

This mounts `downloads/qbittorrent` at `/downloads/qbittorrent` and
`downloads/sabnzbd` at `/downloads/sabnzbd`, writable for moves/deletes during
import. Only the five built-in clients (`qbittorrent`, `transmission`, `deluge`,
`rutorrent`, `sabnzbd`) are valid selections. These directories must already
exist on the selected media claim. Restricted charts require the client claim
paths `downloads/<client>` and manager `storage.media.downloadsSubPath=downloads`
so their subpaths refer to the same data. The rendered Radarr/Sonarr setup script
registers and checks **only selected clients**; shared profile still checks all
five. An existing Radarr/Sonarr database can retain previously registered but
now unselected clients: the script neither disables nor removes them, because
it cannot distinguish user-owned settings from chart-created records. **Before
switching** to restricted, inspect each manager's existing download clients,
disable/remove unselected entries manually, and verify active queues and pending
imports. Include every client you intend to keep; otherwise imports can fail
even though the client remains visible in the application's settings.

Client containers see their own files under `/downloads/`, whereas a restricted
manager sees them under `/downloads/<client>/`. Set/verify a Remote Path Mapping
in each manager for each selected client's hostname, translating remote
`/downloads/` to local `/downloads/<client>/` (for example
`qbittorrent.qbittorrent.svc.cluster.local` → `/downloads/qbittorrent/`). Check
the actual paths reported by the client before enabling imports; the setup
script does not alter user-defined mappings. The library remains mounted
writable at `/media`; changing configured library paths requires matching
application-side settings. Separate
`subPath` mounts may prevent cross-mount hardlinks even on the same NFS export;
test import, rename, and hardlink behavior before relying on hardlinks. The
download clients retain only their own writable download directory. Plex and
Jellyfin retain read-only libraries by default, with writable config and
transcode/cache. Their existing `storage.readOnly` override remains available
when a specific library workflow requires writes. Init containers writing
config still have writable private config; Radarr/Sonarr exporters mount only
read-only config. Restricted Jellyseerr and Jackett have no media volume in
their pods (Jellyseerr normally uses service APIs). Use `profile: shared` for
custom Jellyseerr workflows accessing `/data`. In all cases the chart still
renders the existing shared media PV/PVC when `storage.media.create=true`,
including when no pod mounts it, to avoid Helm deleting managed storage on a
profile change. Set `storage.media.create=false` only for an externally owned
claim that already exists. Restricted mode rejects empty, absolute, dot or
traversal storage subpaths, overlapping effective mount paths and shared
subpaths, known config/library/download/cache/transcode root aliases, and
invalid or duplicate client directory names. Its path checks are lexical;
symlinks on the NFS export and custom directory conventions outside the known
roots still need server-side review before enabling the profile.

The Kubarr worker now defaults to `storage.mountEnabled=false` because its
chart sync, lifecycle reconciliation, and deployments use Kubernetes, Helm and
the database rather than the storage browser. The backend keeps its broad
`/data` mount for the storage file APIs. Set `storage.mountEnabled=true` on the
worker only if a custom workflow explicitly needs the previous mount. Its
media PV/PVC remains rendered regardless of this switch. Provisioning init
containers for Immich model cache and the development charts still need a
root-of-export mount to create their nested directory on a fresh managed NFS
install; they run with a limited CHOWN capability for that operation. Their
application containers mount only their subdirectory. Provision the directories
server-side to eliminate that bootstrap root mount in a separate rollout.

**Boundary:** A PVC `subPath` and a container's read-only mount limit visible
filesystem paths; neither is an NFS authorization boundary. The managed server
defaults to `nfs.permitted="*"` with host networking and an export of `/`.
Apps that can connect to the server may mount the entire export independently.
Existing NetworkPolicies can allow NFS via broad egress and, with host
networking, do not reliably restrict direct export access. For isolation
against an app that can make network connections, provide independently
restricted exports/storage with appropriate client permissions and network
controls, and migrate private data; do not rely on a PVC name, `ReadWriteOnce`,
or a `readOnly` mount alone. `nfs.permitted` can be configured with trusted
client CIDRs when the node/client addresses are known, but it does not grant
per-app directories to pods sharing those clients. Keep Immich PostgreSQL and
Frigate SQLite on their documented local/block claims.

The three storage roles are distinct: **config** is private application state,
**database** is single-writer DB state, and **media** is shared content. This
split option applies to the charts listed above only. Redmine's bundled
PostgreSQL retains its managed-NFS sync/locking requirements; Immich PostgreSQL
and Frigate SQLite must remain on local/block storage. Grafana's SQLite PVC,
Fluent Bit's node-local state, and the managed-NFS backing claim are unchanged.
See the chart-specific README notes for their existing migration protections.

Kubarr installs Immich and Redmine with their bundled databases without
pre-created Secrets: Helm creates strong random database credentials (and a
separate Redmine Rails session key) in their application namespaces. The
Secrets have `helm.sh/resource-policy: keep`; upgrades reuse existing keys via
Kubernetes lookup, and upgrades fail if the generated Secret has disappeared.
Back up `immich-db` and `redmine-credentials` securely along with their database
volumes. After uninstall or an atomic failed install, retained database claims
may still exist: restore the matching Secret before reinstalling if it was
deleted. Never change the password on an initialized PostgreSQL volume without
also changing the database role password. External database configurations
require an explicit `existingSecret` and independently provisioned credentials.

Kubarr's managed NFS PV/PVC (`media-data`, a separate claim in each namespace)
now backs Jenkins home, SCM-Manager home, Redmine attachments and PostgreSQL,
and Immich model cache and uploads. These use separate subdirectories of the
export. Upgrade `system/managed-nfs` to **12.0.0+2** first: its `nfs.sync=true`
default makes the export acknowledge writes only after stable storage. The
Redmine chart checks this on a live managed-NFS deployment and refuses an async
export. An external NFS server must provide equivalent sync, locking, hard
mount and fsync semantics; keep exactly one writer per database and take tested
backups. Root-squashed exports require server-side UID/GID permissions on each
subdirectory. NFS is not a substitute for a database backup.

Two persistent databases **cannot safely follow the NFS-only request**:
Immich upstream explicitly forbids a network share for its PostgreSQL data;
Frigate 0.18 uses SQLite WAL, which is not safe on NFS. Their databases remain
on dedicated local/block claims (cluster default, explicit StorageClass, or
existing claim). Frigate's recordings remain on NFS, and memory-backed shm
and temporary caches remain ephemeral. Verify the default StorageClass is
local/block or specify appropriate claims for those two components; do not
interpret a `ReadWriteOnce` claim alone as proof of local storage.

Existing chart audit: Kubarr's system PostgreSQL and VictoriaMetrics/VictoriaLogs
already mount the managed NFS share; most application config/media charts also
use it. The managed NFS server's **own backing PVC** cannot depend on its own
export, so it remains a bootstrap storage-class claim. Grafana's existing
default SQLite PVC and Fluent Bit's node-local tail-position host directory
are additional exceptions: moving an existing SQLite database or live tail
state onto NFS without a supported migration/locking design risks corruption
or replay. They are left in place pending an explicit storage migration, not
implicitly switched by these new application releases. RAM shm/cache and
temporary directories remain ephemeral.

**Existing local claims are not migrated by a chart upgrade.** Stop writers,
back up the database, home, or attachments and the matching credential Secret,
copy data preserving UID/GID and permissions to the documented NFS subdirectory,
verify the restore, then set `storage.migrationConfirmed=true` for Jenkins,
SCM-Manager, Redmine, or the Immich model cache before upgrading. The new charts
refuse upgrades when an old claim is detected without this acknowledgement;
the old Jenkins/SCM-Manager/Redmine attachments PVCs are rendered with `keep`
to prevent Helm deleting them. Do not delete the original claims until the
migrated application has been verified and independently backed up. To continue
using an old claim instead, set its `existingClaim` explicitly.

To validate fresh local installs through Kubarr, select your local Kind context
and check the default StorageClass with `kubectl get storageclass`; it must
support the two local database exceptions above. Upgrade managed-nfs first,
then install the five charts from Kubarr's
catalog with default values and **no pre-created credentials Secrets**. Check
the application namespaces with `kubectl -n immich get pods,pvc,secret`
(repeat for `frigate`, `redmine`, `jenkins`, and
`scm-manager`), then check each Deployment/StatefulSet rollout and the pod events
and logs if a probe or volume fails. Confirm `immich-db` and
`redmine-credentials` were created automatically, and log in through the gateway
to finish each application's first-run setup. Back up those two Secrets securely
before testing an upgrade or uninstall/reinstall with the same database PVCs.

## Deployment Notes

- Headlamp (`monitoring/headlamp`) is an optional Kubernetes UI at `/headlamp/`.
  Kubarr gateway authentication and Headlamp's per-user Kubernetes token login
  are separate. See the chart README for least-privilege token creation, API
  egress and ephemeral storage requirements.
- Development charts: SCM-Manager (`development/scm-manager`) for Git hosting,
  Redmine (`development/redmine`) for issue tracking, and Jenkins
  (`development/jenkins`) for CI. Each uses a separate NFS subdirectory and is exposed at
  its matching `/scm-manager`, `/redmine`, or `/jenkins` gateway path; see each
  chart README for first-run credentials, database/storage, and agent access.
- Immich (`media-server/immich`) provides photo/video backup on port 2283.
  The bundled database password is generated on first install; its default
  PostgreSQL StorageClass must supply local/block storage. See the chart README for backup
  and media-share requirements.
- Frigate (`media-server/frigate`) provides an NVR on authenticated port 8971.
  The default StorageClass must supply local/block storage for SQLite; configure
  camera-network egress and a writable media share; retrieve its initial admin
  password from pod logs. See the chart README for camera configuration and
  port 5000 isolation.
- Plex and Jellyfin hardware transcoding is opt-in via `gpu.enabled: true`.
  Select `gpu.provider` (`intel`, `nvidia`, `amd`) and optionally override
  `gpu.resourceName` with the extended resource advertised by your device plugin
  (e.g. `gpu.intel.com/xe` instead of the Intel i915 default, or
  `nvidia.com/gpu.shared` instead of `nvidia.com/gpu`). AMD requires an explicitly
  configured resource from a **prepared shared DRM device plugin**; the charts do
  not install a plugin or mount `/dev/dri` themselves. Optional
  `gpu.runtimeClassName` and numeric `gpu.supplementalGroups` can be set for your
  runtime and device permissions. GPU pods use Recreate deployments to release
  a device before replacing the pod; this entails downtime during upgrades.
  NVIDIA media containers set `NVIDIA_DRIVER_CAPABILITIES=video,utility`.
- VPN-enabled charts use the shared `kubarr-common` Gluetun sidecar, private state
  directory, and backend-only control API policy. `vpn.firewallOutboundSubnets`
  defaults to empty: broad RFC1918 bypasses can route a VPN provider's private
  forwarding endpoint outside the tunnel. If an app must call in-cluster services
  (for example, Sonarr calling qBittorrent), configure only the specific pod or
  service CIDRs it needs on its Kubarr VPN provider, then redeploy the assigned
  app. Existing providers retain their saved exceptions; review broad legacy
  ranges before upgrading. NetworkPolicy egress alone does not create these
  Gluetun routing exceptions.
- Fluent Bit sends the `log` field as VictoriaLogs `_msg` for new Loki JSON
  records. Older rows with the default missing-message placeholder remain until
  retention expires.
- Radarr and Sonarr allow their own exporter pod to reach the application HTTP
  port through NetworkPolicy. VictoriaMetrics selects only each annotated Service
  metrics port, avoids scraping those exporters twice via pod discovery, and can
  reach CoreDNS metrics on TCP 9153. These rules do not expose the application
  or metrics ports to unrelated namespaces.

- Fluent Bit chart 5.1.1+2 stores tail positions in a node-local host directory
  (`/var/lib/fluent-bit`) instead of shared NFS. The old position database is not
  migrated, so logs may be replayed once on upgrade. The node-local directory
  persists across pod restarts and is not deleted on uninstall. Its previous
  dedicated NFS PV/PVC is no longer rendered; NFS data itself is not erased.

- Helm 4.3 supports Kubernetes 1.34 through 1.37. Kubarr's release E2E uses 1.35.8.
  Chart API versions remain `v2`; Helm 4 does not require a chart-format migration.
- Core application images default to local repositories with `pullPolicy: Never`.
  Preload them on the nodes or configure an image repository, tag, and pull policy.
- Shared media storage defaults to the managed NFS service. When using existing
  storage, review `storage.media.create` as well as `storage.media.existingClaim`.
- App NetworkPolicies depend on namespace names in `ingressFrom` and `egressTo`.
  Custom Helm release names are supported by the shared policy's pod selector;
  namespace access rules still need to reflect the cluster's topology.
- Managed NFS and VPN sidecars require elevated permissions. Review RBAC,
  credentials, storage access, and network isolation before production use.
