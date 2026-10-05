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

## First installs and persistent credentials

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
- App storage defaults to the managed NFS service. When using existing storage,
  review `storage.media.create` as well as `storage.media.existingClaim`.
- App NetworkPolicies depend on namespace names in `ingressFrom` and `egressTo`.
  Custom Helm release names are supported by the shared policy's pod selector;
  namespace access rules still need to reflect the cluster's topology.
- Managed NFS and VPN sidecars require elevated permissions. Review RBAC,
  credentials, storage access, and network isolation before production use.
