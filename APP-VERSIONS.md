# Application version audit — 2026-10-25

Versions were checked against upstream stable releases and the configured image
publishers. Each changed image tag was verified in its registry; chart versions
and `appVersion` metadata were updated together. Nightly/develop tags are excluded.

## Updated applications

| Chart | Application version | Notes |
| --- | --- | --- |
| Jenkins | 2.580.1, JDK 21 | Current LTS, preserving the existing release channel |
| SCM-Manager | 3.12.2 | Includes the update proposed in PR #2 |
| Redmine | 7.0.2 | Retains Bookworm and PostgreSQL 16 |
| qBittorrent | 5.3.0 | Retains the configuration initialization repair |
| SABnzbd | 5.1.3 | Includes PR #4 |
| Jackett | 0.24.2903 | Latest available LinuxServer image; newer than PR #8 |
| Radarr | 6.4.4 | Stable image, not Radarr 7 nightly |
| Sonarr | 4.0.20 | Stable image, not Sonarr 5 develop |
| Jellyfin | 12.3 | Pinned LinuxServer build `12.3ubu2604-ls56` |
| Plex | 1.43.5 | Latest available LinuxServer stable image |
| Frigate | 0.18.2 | Official image |
| Immich | 3.3.5 | Server and machine-learning versions match; Valkey pinned to 9.1.2 |
| Jellyseerr → Seerr | 3.5.1 | Official Seerr image; existing release identity/config claim retained |
| Headlamp | 0.46.0 | Official image |
| Fluent Bit | 5.1.5 | Stable image |
| Grafana | 13.2.4 | Stable image |
| VictoriaLogs | 1.53.0 | Recreate rollout avoids concurrent storage lock holders |
| VictoriaMetrics | 1.154.1 | Stable image |
| Cloudflared | 2026.10.3 | Stable image |
| cert-manager | 1.21.3 | Wrapper and upstream dependency updated together |
| Kubarr DNS webhook | 0.3.4 | Published GHCR image replaces local-only image defaults |

## Already current or constrained by the publisher

- Kubarr backend/frontend/worker: published 0.3.4.
- Deluge: LinuxServer stable 2.2.0.
- Transmission: upstream 4.1.4 exists, but LinuxServer has not published that tag;
  4.1.3 remains its latest available stable image.
- ruTorrent: the configured LinuxServer publisher's final image remains
  `v3.10-ls122` (2021); a maintained replacement requires a separate image and
  storage-layout migration. This is not a current maintained ruTorrent build.
- OpenResty: 1.29.2.5 remains the current configured release line.
- Gluetun: v3.41.3; exportarr: v2.3.0; qBittorrent exporter: v1.7.0;
  Transmission exporter: 0.3.0. No newer published stable release was found.
- PostgreSQL: 16.15 is the current patch of the deployed major version. Changing
  database majors requires a data migration, not an image-only upgrade.
- Immich's PostgreSQL 14/VectorChord image matches its v3.3.5 upstream Compose
  configuration; it must not be replaced with a generic newer PostgreSQL image.
- Managed NFS: retain the Ganesha implementation and its compatible provisioner;
  the legacy kernel image's final published tag remains `12`.

## Pull requests reviewed first

- [#2](https://github.com/smokeythebandit/kubarr-charts/pull/2): SCM-Manager update included.
- [#4](https://github.com/smokeythebandit/kubarr-charts/pull/4): SABnzbd update included.
- [#7](https://github.com/smokeythebandit/kubarr-charts/pull/7): proposes the weekly
  Jenkins channel; current LTS/JDK21 is selected instead.
- [#8](https://github.com/smokeythebandit/kubarr-charts/pull/8): superseded by the
  newer available Jackett image.
- [#9](https://github.com/smokeythebandit/kubarr-charts/pull/9): superseded by Immich
  3.3.5; both server and ML images are updated, fixing its version-mismatch failure.
- [#3](https://github.com/smokeythebandit/kubarr-charts/pull/3): GitHub Actions
  dependency updates, rather than application version updates.

## Major-version migration notes

### Jellyfin 10.11 → 12

Back up the stopped application's complete private `/config` volume before
upgrading. Jellyfin 12 migrates its database and changes plugin/API compatibility;
restore the backup as well as the old image if rolling back. The deployed test
instance migrated successfully and its native login UI rendered through Kubarr.

### Jellyseerr 2.7.3 → Seerr 3.5.1

The chart, values root (`jellyseerr`), namespace, service, and private config claim
retain their existing names so the release can be upgraded in place. The catalog
display name becomes **Seerr**. Seerr automatically migrates the existing data in
`/app/config`; back up that claim while the old application is stopped first.
The official image runs as UID/GID 1000, with the existing `fsGroup: 1000`.
Node runs directly so Kubernetes termination signals reach the server.

A disposable Docker test started Jellyseerr 2.7.3 with a fresh database, stopped it,
then started Seerr 3.5.1 against the same config volume as UID 1000. Both status
endpoints reported the expected versions and the migration succeeded.

Reference: <https://docs.seerr.dev/migration-guide/>.
