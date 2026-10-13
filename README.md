# Kubarr Charts

Helm charts for Kubarr and the applications it manages. Charts are grouped under
`development/`, `download-client/`, `indexer/`, `media-manager/`,
`media-server/`, `monitoring/`, and `system/`. There is no umbrella `kubarr`
chart; use the Kubarr CLI to bootstrap the platform and its storage, database,
monitoring, gateway, and worker services.

## Declarative storage (breaking upgrade)

**Read [STORAGE-MIGRATION.md](STORAGE-MIGRATION.md) before upgrading an existing
installation.** Every shared-NFS data mount is now an explicit list entry in
`storage.mounts` (or `storage.workloads.<component>.mounts`). The 11 app charts
whose `/config` was on shared NFS now use a private claim. The chart cannot
copy existing data: stop writes, back up, migrate and verify it before setting
`storage.config.migrationConfirmed=true` on upgrade. Old role/layout/path
settings fail rendering, including those reused from an earlier Helm release.

The managed-NFS server's backing PVC remains on its bootstrap StorageClass.
Immich PostgreSQL and Frigate SQLite need local/block storage; Grafana SQLite
stays on its dedicated PVC, and Fluent Bit positions stay node-local. Verify
the cluster's **default StorageClass** is appropriate for newly created private
config claims, or set `storage.config.storageClassName` or a pre-provisioned
`storage.config.existingClaim` in each app chart. Managed NFS does not provide
that default private-config StorageClass. All other chart defaults are in
their `values.yaml` files; the migration document lists claim and directory
preservation, first-upgrade and `storage.media.create=false` handoff steps.

## Local validation

Use Helm 4.3.0 (the CI version), LuaJIT, Python 3, and PyYAML 6.0.3. Install
Python dependencies in a virtual environment if your operating system requires one.

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

NetworkPolicy regression tests verify selectors and allowed HTTP container
ports, not live CNI enforcement. Gateway tests exercise rendered Lua routing
with mocked OpenResty APIs; they do not replace live proxy testing.

For packaged-chart validation, package every chart into an empty directory
and set `CHART_PACKAGES_DIR` to its absolute path before running the tests.
Missing or extra `.tgz` files fail validation. Tests never fall back to chart
source in package mode. Package tests check metadata and application renders
against Kubernetes 1.35.8, not API-server admission.

## Dependencies and releases

Dependabot owns automated chart and GitHub Actions version PRs; Renovate is
disabled in `renovate.json` to avoid duplicate PRs (its former rules remain
for reference). Once `.github/dependabot.yml` is merged into the repository's
default branch and Dependabot version updates are enabled, it checks chart
`Chart.yaml` dependencies and image references in `values.yaml` across all
seven chart groups every Monday at 09:00 UTC, and checks GitHub Actions on
the same schedule. Version PRs are limited to five open Helm PRs and two
Actions PRs; Actions updates are grouped. No updates are auto-merged.
Dependabot security updates, where supported and enabled on GitHub, are
separate from the weekly version schedule; GitHub does **not** support
Dependabot security updates for Helm charts. These checks do not cover
literal images in templates; local `kubarr-common` and Kubarr-built images
are excluded and must be updated manually.

Review each chart update PR before merging: for a primary app image, adjust
`appVersion` and the chart version's normalized app-version core; for a
sidecar, bump the chart's `+revision` without changing the primary app
version. Update locks and run the chart tests. Dependabot's Helm support is
documented for Helm 3; this repository validates with Helm 4.3, so confirm
generated changes build and render correctly under Helm 4.

`system/kubarr-common` supplies shared Helm helpers. When changing it, bump
its version and each affected consumer's dependency and chart version, then
run `helm dependency update --skip-refresh <chart-directory>`. Include the
resulting `Chart.lock` changes. Dependency archives under `charts/` are
generated for local validation and publishing, ignored by Git, and must not
be committed.

Pull requests run lint, packaging, and regression tests. Main-branch pushes
run the same validation before publishing OCI charts to
`ghcr.io/<repository-owner>/kubarr-charts`. Validation uploads a
`validated-charts` artifact. Publishing downloads and pushes those exact
archives, without checking out or rebuilding chart sources. Existing versions
are skipped, so every published chart change requires a chart version bump.
Application chart versions use the normalized `appVersion` as their core and
build metadata as the chart revision.

## Deployment notes

- Headlamp (`monitoring/headlamp`) is an optional Kubernetes UI at
  `/headlamp/`. Gateway authentication and its per-user Kubernetes token login
  are separate; see its chart README.
- SCM-Manager, Redmine, and Jenkins use explicit NFS directories for home,
  attachments and, where selected, bundled PostgreSQL. Preserve their existing
  claim migration guards and generated credential Secrets when upgrading.
- Immich provides photo/video backup on port 2283 and requires a local/block
  claim for bundled PostgreSQL. Frigate provides an NVR on authenticated port
  8971 and requires a local/block claim for SQLite.
- Plex and Jellyfin hardware transcoding is opt-in via `gpu.enabled: true`.
  Select `gpu.provider` (`intel`, `nvidia`, `amd`) and optionally override
  `gpu.resourceName`, `gpu.runtimeClassName`, and `gpu.supplementalGroups`.
  AMD requires a prepared shared DRM device plugin. GPU pods use Recreate
  deployments; NVIDIA containers request `video,utility` driver capabilities.
- VPN-enabled charts use the shared Gluetun sidecar and backend-only control
  API policy. `vpn.firewallOutboundSubnets` defaults to empty; add only the
  specific pod/service CIDRs required for in-cluster endpoints. NetworkPolicy
  egress alone does not create VPN routing exceptions.
- Fluent Bit sends `log` as VictoriaLogs `_msg` for new Loki JSON records and
  stores tail positions in the node-local `/var/lib/fluent-bit` directory.
- Radarr/Sonarr exporter pods reach the application port through NetworkPolicy.
  VictoriaMetrics selects annotated Service metrics ports and can reach
  CoreDNS metrics on TCP 9153.
- Helm 4.3 supports Kubernetes 1.34 through 1.37; Kubarr release E2E uses
  1.35.8. Chart API versions remain `v2`.
- Core application images default to local repositories with `pullPolicy:
  Never`. Preload them or configure image repository, tag, and pull policy.
- App NetworkPolicies depend on namespace names in `ingressFrom`/`egressTo`.
  Custom release names are supported by pod selectors; namespace access
  still needs to reflect the deployment topology.

For an archival record of the old, **unsupported** layout/profile settings,
see [README-pre-declarative.md](README-pre-declarative.md). Use the current
migration document and chart `values.yaml` for all new installations/upgrades.
