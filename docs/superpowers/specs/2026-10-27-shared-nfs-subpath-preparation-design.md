# Shared NFS subpath preparation and library path alignment

Date: 2026-10-27
Repositories: `kubarr-charts` (primary), `kubarr-cli` (bootstrap pins)

## Problem

Every NFS-consuming chart mounts one shared export (`media-data`) and selects a
`subPath` out of it. Kubelet creates a missing `subPath` directory **as root**.
A normal export squashes root, so kubelet cannot create the directory and the
Pod stays in `CreateContainerConfigError` with:

```
failed to create subPath directory for volumeMount "media" of container "<app>"
```

Preparation is currently per chart, and it is inconsistent in two ways:

1. Seven charts prepare their directories correctly in an unprivileged init
   container. The pattern is copy-pasted in each one, which is how
   `system/postgresql` came to be missed until it blocked every installation.
2. Four charts prepare their directories in an init container that runs as
   **uid 0 with `CAP_CHOWN`** and executes `mkdir -p && chown`. Root squashing
   defeats both operations, so these charts are broken on a root-squashed
   export as surely as the charts with no preparation at all.

Eight further charts with writable `subPath` mounts have no preparation.

Separately, Sonarr and Radarr each mount the whole library tree at `/media`,
while download clients mount their own directory at `/downloads`. The differing
in-container paths require a Sonarr/Radarr remote path mapping to reconcile, and
neither chart restricts itself to the part of the library it owns.

## Goals

- One definition of NFS subpath preparation, in `kubarr-common`, used by every
  consumer.
- No preparation step runs as root or needs `CAP_CHOWN`.
- Sonarr sees only `/media/shows`; Radarr sees only `/media/movies`; both at a
  path identical to their location in the export.
- Jellyfin and Plex see the whole library read-only at `/media`.
- Sonarr and Radarr register their root folder without operator clicks.

## Non-goals

- Migrating existing libraries. The new library defaults suit a fresh install or
  an export whose contents already live under `media/shows` and `media/movies`.
  No migration guidance is written in this change.
- Restructuring mounts so downloads and library share one filesystem for
  hardlinks and atomic moves. That is a larger change, tracked separately.
- `system/managed-nfs`, `monitoring/grafana`, `system/kubarr-frontend` and
  `system/openresty`, which do not depend on `kubarr-common`.

## Design

### The helper

`kubarr-common.storage.prepareMounts`, defined in
`system/kubarr-common/templates/_storage.tpl` beside `appMounts`:

| Input | Meaning |
| --- | --- |
| `root` | The chart context, as the other storage helpers take it |
| `mounts` | Optional explicit mount list; defaults to `.Values.storage.mounts` |
| `uid` | Optional uid that will own the created directories; default `1000` |

Behaviour:

- Select mounts whose `mode` is `read-write` **and** whose `source` is not `"."`.
  A `"."` source mounts the export root, which already exists, so there is
  nothing for kubelet to create.
- Render nothing at all when that selection is empty.
- Otherwise render one init container:

```yaml
- name: prepare-media-directories
  image: busybox:1.37.0            # pinned once, in kubarr-common values
  command: [sh, -ec, 'umask 0002; for directory do mkdir -p -- "$directory"; test -w "$directory"; done', --]
  args: ["/nfs/<source>", ...]     # one per selected mount
  securityContext:
    runAsUser: <uid>
    runAsGroup: 1000
    runAsNonRoot: true
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: [ALL]
  volumeMounts:
    - name: media
      mountPath: /nfs
```

Creating each directory as the application's own uid with group 1000 and
`umask 0002` produces correct ownership on creation. No `chown` is needed, so
nothing in the step depends on privileges that root squashing removes. The
`test -w` check turns a silently unwritable directory into a failed init
container rather than a confusing application error later.

A companion `kubarr-common.storage.mediaGroup` emits
`supplementalGroups: [1000]` for the Pod security context, so an application
whose uid is not 1000 can still reach directories owned by the media group.

### Adoption

Every workload below mounts the `media` volume, so the helper can rely on its
presence.

Replacing an existing correct copy, render unchanged:

| Chart | uid |
| --- | --- |
| `download-client/qbittorrent` | 1000 |
| `media-manager/sonarr` | 1000 |
| `media-manager/radarr` | 1000 |
| `media-server/jellyfin` | 1000 |
| `monitoring/victorialogs` | 1000 |
| `monitoring/victoriametrics` | 65534 |
| `system/postgresql` | 999 |

Replacing a root-running `mkdir && chown` container, which drops uid 0 and
`CAP_CHOWN`:

| Chart / workload | uid |
| --- | --- |
| `development/jenkins` | 1000 |
| `development/scm-manager` | 1000 |
| `development/redmine` (`prepare-files`) | 999 |
| `development/redmine` (postgres workload) | 999 |
| `media-server/immich` (machine-learning workload) | 1000 |

Gaining preparation for the first time, all uid 1000:
`download-client/deluge`, `download-client/rutorrent`,
`download-client/sabnzbd`, `download-client/transmission`,
`media-manager/jellyseerr`, `media-server/frigate`, `media-server/immich`
(main workload), `media-server/plex`.

Unchanged: `system/kubarr-backend`, whose only writable mount is the export
root.

`media-server/immich/templates/ml.yaml` and
`development/redmine/templates/postgres.yaml` pass explicit mount lists to
`appMounts` today; they call the helper the same way, with their own list, so
each workload prepares exactly the directories it mounts.

### Library paths

| Chart | Mount | Mode |
| --- | --- | --- |
| `media-manager/sonarr` | `media/shows` → `/media/shows` | read-write |
| `media-manager/radarr` | `media/movies` → `/media/movies` | read-write |
| `media-server/jellyfin` | `media` → `/media` | read |
| `media-server/plex` | `media` → `/media` | read |

The validator already accepts nested `source` and `target` values, and neither
new target overlaps `/config` or `/downloads/*`. Sonarr and Radarr keep their
five download-client mounts unchanged, so the existing qBittorrent remote path
mapping continues to apply.

Jellyfin and Plex change target only; the whole tree stays read-only, and they
observe `/media/shows` and `/media/movies` as written by the two managers. No
template in either chart refers to the old `/data` target.

### Root folder registration

`ensure_root_folder()` is added to the Sonarr and Radarr setup scripts,
modelled on the existing `ensure_qbittorrent_path_mapping()` in the same file:

- `GET /api/v3/rootfolder`; if any entry's path, trailing slash trimmed, equals
  the chart's library target, return success and change nothing.
- Otherwise `POST {"path": "<library target>"}`.
- Return non-zero on a failed or malformed read, or a failed write, so the next
  reconciliation pass retries rather than reporting success.
- Call it once per pass, after `is_api_ready`, before the download-client loop.

The path is templated from the chart's own mount named `library`, so the script
and the mount cannot drift. If no such mount exists, the step is omitted from
the rendered script. An operator's own root folder is never replaced; a missing
one is added. Registration depends on the directory existing and being
writable, which is what the preparation init container guarantees.

## Versioning and release

`kubarr-common` goes from `0.4.0` to `0.5.0`. `tests/test_dependencies.py`
requires every consumer's `Chart.yaml` dependency pin **and** `Chart.lock` to
equal the current common version, so all **23** consumers are updated with
`helm dependency update`. A dependency pin is chart content, and published
revisions are immutable, so **all 23 bump their own version** — including
`indexer/jackett` and `monitoring/headlamp`, which change for the pin alone and
gain no preparation container.

Two of those versions need a deliberate choice: `system/kubarr-backend` and
`system/kubarr-worker` currently keep `version` equal to `appVersion` (`0.3.4`).
Their application images do not change here, so they take build metadata
(`0.3.4+1`) rather than claiming a new application version.

Six installer-pinned bootstrap charts depend on `kubarr-common`:
`system/postgresql`, `monitoring/fluent-bit`, `monitoring/victorialogs`,
`monitoring/victoriametrics`, `system/kubarr-worker` and
`system/kubarr-backend`. Their new revisions must be published first, then
pinned in `kubarr-cli`:

- `src/charts.rs` — `pinned_version` entries
- `src/bootstrap.rs` — the expected-pin test table
- `CHANGELOG.md`, `Cargo.toml` — CLI `0.2.10`

`system/managed-nfs`, `monitoring/grafana`, `system/kubarr-frontend` and
`system/openresty` do not depend on common and keep their current pins.

## Testing

New `tests/test_prepare_mounts.py`, asserting across every consumer chart:

- each writable, non-root mount source appears exactly once as a `/nfs/<source>`
  argument;
- `runAsUser` matches the chart's documented uid and `runAsGroup` is 1000;
- no preparation container runs as root, requests `CAP_CHOWN`, or allows
  privilege escalation;
- a chart whose only writable mount is the export root renders no init
  container;
- the rendered command, executed locally twice against a temporary directory,
  is idempotent and leaves the directory group-writable (`0775`).

Existing `tests/test_monitoring_startup.py` and
`tests/test_system_database_startup.py` must pass **unmodified**. They assert
the current inline shape, so their continued success is the evidence that the
seven migrations did not change what those charts render.

Extended `tests/test_arr_download_paths.py`, reusing its harness that sources
rendered script functions against a stub API:

- the root folder is created once across two reconciliation passes, posting
  `/media/shows` for Sonarr and `/media/movies` for Radarr;
- an existing root folder, with or without a trailing slash, produces no POST;
- an unrelated root folder does not suppress ours;
- a failed read, a malformed read, and a failed write each produce a non-zero
  return and no silent success.

Plus a values-level assertion that each library mount's `target` equals `/` and
its `source`, which is the identical-path property this change exists to
establish.

Finally, `kubarr-cli`'s installer acceptance journey runs in published mode
against the new chart revisions. It covers `postgresql`, the two Victoria
components, `kubarr-worker` and a real Sonarr installation, so it exercises both
a migrated bootstrap chart and a migrated application chart end to end.

## Risks

- **Wide mechanical change.** 23 locks and 23 chart versions move at once, while
  only 18 charts actually gain or change a preparation container. The two
  untouched startup tests and the published-mode acceptance run are the guards
  against a silent render change.
- **Ordering.** Charts publish before the CLI pins can reference them, so the
  release is two steps: publish charts, then pin and release the CLI.
- **Existing libraries.** `/media` disappears from Sonarr and Radarr, and
  Jellyfin and Plex move from `/data` to `/media`. Installations with data under
  the old layout need their files moved and their root folders and libraries
  re-pointed. This is accepted and deliberately undocumented here.
- **Group assumption.** Preparation assumes the export is group-writable by gid
  1000, which is what the acceptance fixture and the storage check establish. An
  export owned otherwise fails the `test -w` check, visibly, in the init
  container.
