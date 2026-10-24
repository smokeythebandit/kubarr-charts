# Declarative storage and breaking upgrade

## Jellyseerr private-config correction

Chart `2.7.3+9` mounts the private configuration claim at `/app/config`, which
Jellyseerr actually uses. Revision `2.7.3+8` mounted it at `/config`, leaving the
application's configuration in the container filesystem. If that revision is
already running, back up its live `/app/config` data and migrate it into the
private claim before upgrading/restarting; the chart cannot recover ephemeral
configuration after the old container is removed.

## Shared mounts

All NFS-consuming charts now declare their data mounts in `values.yaml`. The
only supported shared-export mount entry is `{name, source, target, mode}`:

```yaml
storage:
  media:
    create: true
    existingClaim: media-data
    nfs: {} # optional server, path, size; unchanged PV/PVC names
  mounts:
    - {name: movies, source: media/movies, target: /data/movies, mode: read}
    - {name: incoming, source: downloads/qbittorrent, target: /downloads/qbittorrent, mode: read-write}
```

`source` is a nonempty relative subdirectory of the shared NFS export, `target`
is an absolute container path, and `mode` is exactly `read` or `read-write`.
The name identifies an entry, **not** a PVC. Empty `mounts: []` exposes no NFS
data in that container; the existing Helm-managed media PV/PVC is still
rendered **only for workloads where NFS data is optional** (for example Jackett
or Kubarr worker). Jenkins/SCM-Manager home, Redmine attachments and database,
Immich uploads/model cache, Frigate recordings, each download client's own
`/downloads`, system PostgreSQL, and VictoriaMetrics/Logs require
their documented writable persistent targets; omission or read-only mounting
fails rendering. Lists replace the chart defaults in full. The Kubarr backend storage
API is the sole explicit export-root exception (`source: .` at `/data`), and
the worker may opt in to that same mount by providing a list. Set up directories
and UID/GID ownership on the server before using `subPath` mounts. Mounts do
not enforce server-side NFS permissions or guarantee cross-mount hardlinks.

Separate workloads use `storage.workloads.<component>.mounts` with the same
entry shape: Immich `machineLearning` mounts its model cache; Redmine
`postgres` mounts its bundled database. Only that workload receives its list.
The main `storage.mounts` never automatically enters an exporter, init
container, sidecar, or other workload. Where a managed-NFS install must create
a nested directory, a documented prepare init container temporarily mounts the
export root (`/nfs`); provide directories/permissions on an external server to
avoid that bootstrap mount. The bootstrap selects the persistent mount by its
required target, regardless of list order. Redmine's install-time credential guard also
inspects its database directory from an explicitly read-only root mount.

| Old storage setting | New selection |
| --- | --- |
| `storage.layout=legacy/split`, `profile`, `access.downloadClients` | Remove; use concrete `storage.mounts` lists. Radarr/Sonarr register only built-in clients with an exact writable `downloads/<client>` -> `/downloads/<client>` entry. |
| `storage.media.*SubPath`, `*MountPath`, `storage.mountPath`, `storage.readOnly`, `storage.mountEnabled` | Remove; set each entry's `source`, `target`, `mode`, or an empty list. |
| `storage.migrationConfirmed` in the 11 split-config apps | `storage.config.migrationConfirmed`; acknowledge only **after** copying/validating private config. |
| `storage.home.subPath` (Jenkins/SCM-Manager), `storage.attachments.subPath` (Redmine), `database.bundled.persistence.subPath` (Redmine), `machineLearning.cache.subPath` (Immich) | Move the old effective directory to the matching main or component mount's `source`. |

The 11 Radarr/Sonarr/Jellyseerr/Jackett/Plex/Jellyfin/download-client charts
always mount a separate RWO config PVC at `/config` (Jellyseerr: `/app/config`).
Fresh installs create `<chart-fullname>-config` (retained by Helm) using the
cluster default StorageClass unless `storage.config.storageClassName` is set.
**Before first install, verify a default StorageClass exists and is suitable
for private, single-writer config; otherwise set a suitable class or provide
`storage.config.existingClaim` with `create: false`.** Kubarr managed NFS does
not supply a default config StorageClass;
`create: false` plus `existingClaim` selects a pre-provisioned private PVC.
The old config lives under `config/<chart-name>` on `media-data` by default.
For **every existing installation**, stop the application and exporters, back
up the export and credentials, create/select the destination claim, and copy
the *contents* (including hidden files and ownership) into its root. Verify
the restore and app UID/GID. Only then upgrade with
`storage.config.migrationConfirmed=true`. Reconfirm on subsequent upgrades.
The Helm guard cannot inspect copied data. Keep the old data and independent
backup until recovery has been tested; a Helm rollback does not copy it back.
Use a freshly reviewed values file and `--reset-values` rather than
`--reuse-values`: removed keys fail rendering even if their old value was null.
Kubarr's normal app-update endpoint uses `--reuse-values` and intentionally
cannot perform the first breaking migration or assert that data was copied.
For that one upgrade, use a manual Helm upgrade of the same release/namespace
with `--reset-values`, a reviewed values file containing your previous secrets,
storage claim names, network/VPN/GPU customizations, explicit mount lists and
`storage.config.migrationConfirmed=true` **only after verification**. Verify
the release and stored new values before resuming Kubarr-managed updates; the
endpoint can then reuse the migrated values. Kubarr surfaces the Helm migration
error with these instructions rather than automatically resetting old values.

The shared-claim backend accepts `storage.media.nfs.server` (nonempty host/IP),
`path` (absolute NFS export directory, default `/`), and `size` (positive
Kubernetes quantity); invalid or incorrectly typed overrides fail rendering.
Freshly rendered managed PV **and** PVC resources are annotated
`helm.sh/resource-policy: keep`. Changing `storage.media.create=true` to
`false` on an installed release may remove an older, unprotected Helm-owned
resource. A live upgrade guard rejects that switch while either live resource
belongs to this release and lacks `keep`: first upgrade with `create=true` and
the new chart to attach `keep` to both objects, verify the annotations on the
PV/PVC, then upgrade with `create=false` against the intended existing claim.
That retained claim/PV will survive Helm uninstall; manage its eventual cleanup
and ownership explicitly. An offline `helm template --is-upgrade` has no live
Kubernetes lookup and **cannot verify this guard**; check both live objects
before proceeding. Do not use this toggle to move data or adopt another
release's claim implicitly.

Defaults of all chart cohorts are explicit in their `values.yaml`: managers
declare library and each of five download directories; clients only their
own downloads; Plex/Jellyfin library read-only plus transcode/cache writable;
Jellyseerr exposes `media` while Jackett exposes no NFS data. Immich declares
uploads plus the ML model directory, Frigate recordings, Jenkins/SCM-Manager
home, Redmine attachments plus bundled DB, system PostgreSQL and Victoria
Metrics/Logs data, and Kubarr backend its storage-browser export root. Worker
defaults to `[]`. Custom release names do **not** change source directories:
set unique explicit sources when installing more than one release per export.

Existing claims for Jenkins/SCM-Manager home, Redmine attachments/DB and
Immich ML need the same stop/backup/copy/verify treatment when changing their
backend; their prior migration guards and kept claims still apply. Leave
Immich PostgreSQL and Frigate SQLite on local/block claims, keep Grafana SQLite
on its own PVC, Fluent Bit tail positions on its host path, and managed-NFS's
backing PVC on its bootstrap StorageClass. Do not migrate these databases to
the shared export by changing an NFS mount. Preserve generated credential
Secrets with database backups. Media claim names and static NFS PV naming stay
`media-data` / `kubarr-media-<namespace>-media-data` by default.

Kubarr API/CLI bootstrap now supplies shared claim and NFS server settings,
letting charts own their mount defaults; it no longer sends obsolete mount
path keys. This is a chart-values contract change, not a live data migration.
