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

### Jellyfin chart 12.3.0+20: sign in with Kubarr

Installs the Kubarr plugin (`ghcr.io/smokeythebandit/kubarr-jellyfin`) through
an init container. The plugin replaces the `api-key` sidecar and completes the
startup wizard. Kubarr users then sign in with their Kubarr account, both in
the browser and in native apps. On first sync, any existing local Jellyfin
account whose name matches a Kubarr user is switched to Kubarr sign-in, and
its Jellyfin password stops working. Accounts with no matching Kubarr user are
not touched. Set `kubarrPlugin.enabled=false` to keep the previous behaviour.

The gateway now lets requests without a Kubarr session reach Jellyfin
(`kubarr.io/gateway-auth: app-native`), and Jellyfin authenticates them
itself. This is what native clients need. Kubarr throttles failed password
checks.

### OpenResty gateway 1.29.2+5.20: X-Kubarr-User

The gateway forwards the signed-in Kubarr username as `X-Kubarr-User` to apps
whose chart sets `kubarr.io/user-header`, and always overwrites the header.
The `/api/` location, which also serves host-routed apps' own APIs, now sets
`X-Kubarr-Identity` and `X-Kubarr-User` from Kubarr's answer as well. It used
to pass client-supplied values through.

### Jellyfin chart 12.3.0+21: Jellyfin Enhanced

Installs [Jellyfin Enhanced](https://github.com/n00bcodr/Jellyfin-Enhanced)
12.15.0.0 (GPL-3.0) through an init container. The container downloads the
pinned release zip, verifies its sha256, and unpacks it into the plugin
folder. If the download or the check fails, Jellyfin starts without the
plugin. Jellyfin Enhanced injects its script while serving `index.html`, so
nothing is written to the web folder. Kubarr fills in its Seerr, Sonarr and
Radarr settings using each app's existing API key, so no key is rotated.
Values changed in the plugin's own settings page are kept. Jellyfin may now
reach Seerr, Sonarr and Radarr: Sonarr 4.0.20+27, Radarr 6.4.4+27 and Seerr
3.5.1+19 accept it. To read each key, those charts let Kubarr's worker exec
into their own pods, in that namespace only (`kubarrConfigAccess`). Set
`jellyfinEnhanced.enabled=false` to leave the plugin out.

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

## New application — 2026-11-10

| Chart | Category | Image | Application version |
| --- | --- | --- | --- |
| Dispatcharr | media-server | `ghcr.io/dispatcharr/dispatcharr` | 0.33.0 |

Dispatcharr (AGPL-3.0) manages IPTV provider accounts (M3U or Xtream Codes),
their EPG and connection limits. It serves the channels as an HDHomeRun tuner,
plus M3U and XMLTV outputs. It runs all-in-one: PostgreSQL and Redis share the
pod, on a private claim. Its entrypoint starts as root and drops to its own
users, with only baseline capabilities. Kubarr's worker adds its tuner and
guide to Jellyfin's Live TV, and Jellyfin 12.3.0+22 may reach it. Provider
credentials are entered in Dispatcharr's UI. The UI serves from the root of its
host, so it needs its own (sub)domain in Kubarr's Domains settings.
From 0.33.0+3 the chart takes the shared `gpu` values, so Kubarr's install
wizard offers a GPU for ffmpeg stream profiles that transcode. Plain
pass-through needs no GPU.
From 0.33.0+4 Kubarr signs you in to Dispatcharr. The gateway sends the Kubarr
username in `X-Kubarr-User` (`kubarr.io/user-header`), and Dispatcharr's
Reverse Proxy Auth trusts it from private peers. Only the gateway may reach
the UI port (9191). Jellyfin gets its own tuner port (9192): an nginx sidecar
there serves only `/hdhr`, `/output` and `/proxy`, deletes `X-Kubarr-User`,
and answers 403 for everything else. Jellyfin therefore cannot reach the
sign-in or assert a user.

## New application — 2026-11-07

| Chart | Category | Image | Application version |
| --- | --- | --- | --- |
| Unpackerr | download-client | `ghcr.io/unpackerr/unpackerr` | 1.0.1 |

The `v1.0.1` tag (2026-10-29) was verified on GHCR. Unpackerr runs in folder
mode: each writable `/downloads/*` mount is polled (NFS carries no file events
between nodes) and archives are extracted next to themselves, so the managers
import them on their next download check without sharing an API key. Checked
with the image: a ZIP dropped into a polled folder was extracted in place and
the archive kept. Unpackerr 1.0 replaced `[[folder]]` lists with keyed
sections, hence `UN_FOLDER_<key>_*` variables. Its web server stays off: it
would require a password and logs a generated admin key.

### Unpackerr chart 1.0.1+3: web UI

Unpackerr's web UI is on, at `/unpackerr` behind Kubarr's gateway, and the
dashboard offers it as an app. It shows the queue, history and logs. Its own
login is off (`noauth`). The network policy lets only the gateway connect, and
the gateway requires a Kubarr session that is allowed to open Unpackerr.
`noauth` only trusts requests from `unpackerr.web.trustedProxies` (the private
ranges by default, since the gateway's pod IP changes). The live updates
WebSocket checks the browser's Origin against the Host, and the gateway
forwards Host without a port, so live updates work on a domain on the default
port but not on a `host:port` address. Settings changed in the UI do not
persist, because Kubarr configures Unpackerr through its environment. Set
`unpackerr.web.enabled=false` to turn the UI off.

### Unpackerr chart 1.0.1+2: follow Sonarr, Radarr and Lidarr

Unpackerr now follows the Starr apps' download queues by default
(`starrLink`). Kubarr's worker reads each installed app's existing API key and
writes `UN_SONARR_0_*`, `UN_RADARR_0_*` and `UN_LIDARR_0_*` into the
`kubarr-unpackerr-starr` Secret, then restarts Unpackerr when those values
change. Unpackerr extracts what the apps are waiting for and removes the
extracted copy after they import it. The archive is kept, so torrents keep
seeding. Folder watching turns off while the link is on, because watching the
same folders as well would extract twice. Set `unpackerr.folderWatch=true` to
keep it, or `starrLink.enabled=false` to return to folder mode. Unpackerr may
now reach Sonarr, Radarr and Lidarr, and Sonarr 4.0.20+28, Radarr 6.4.4+28
and Lidarr 3.1.6+12 accept it. Lidarr also lets Kubarr's worker read its key
by exec, in its own namespace only. The chart moves to kubarr-common 0.8.4.

## New applications — 2026-11-01

Thirteen charts added. Each image tag below was verified against its registry
(Docker Hub tag API, GHCR `tags/list`, or `registry.k8s.io`) before pinning;
nightly, develop and beta channels were excluded, as everywhere else here.

| Chart | Category | Image | Application version |
| --- | --- | --- | --- |
| Prowlarr | indexer | `linuxserver/prowlarr` | 2.7.0 |
| FlareSolverr | indexer | `ghcr.io/flaresolverr/flaresolverr` | 3.5.2 |
| Lidarr | media-manager | `linuxserver/lidarr` | 3.1.6 |
| Bazarr | media-manager | `linuxserver/bazarr` | 1.6.2 |
| Recyclarr | media-manager | `ghcr.io/recyclarr/recyclarr` | 8.8.1 |
| Navidrome | media-server | `deluan/navidrome` | 0.64.2 |
| Audiobookshelf | media-server | `ghcr.io/advplyr/audiobookshelf` | 2.37.2 |
| Komga | media-server | `gotson/komga` | 1.29.2 |
| Calibre-Web Automated | media-server | `ghcr.io/crocodilestick/calibre-web-automated` | 4.0.8 |
| Tdarr | media-server | `ghcr.io/haveagitgat/tdarr` + `tdarr_node` | 3.04.00 |
| kube-state-metrics | monitoring | `registry.k8s.io/kube-state-metrics/kube-state-metrics` | 2.20.0 |
| vmalert | monitoring | `victoriametrics/vmalert` | 1.154.1 |
| Alertmanager | monitoring | `prom/alertmanager` | 0.34.2 |

### Version notes

- **Tdarr** publishes zero-padded tags (`3.04.00`), which is not valid SemVer and
  which the CI version check does not normalize. The chart is versioned `3.4.0`
  with the literal tag in `values.yaml`, following the `openresty` precedent
  (`1.29.2.5` → `1.29.2+5.12`).
- **vmalert** is pinned to 1.154.1 to match the VictoriaMetrics server's
  appVersion rather than the newer 1.155.0; the two share the MetricsQL
  implementation.
- **Bazarr ships no metrics exporter.** exportarr has a `bazarr` subcommand, but
  its only key source here is `--config`, an *arr `config.xml` parser. Bazarr
  keeps a runtime-generated `auth.apikey` in `config.yaml`, so the exporter would
  fail its own key validation at startup.
- **Readarr was not added.** Upstream retired it; ebooks are covered by
  Calibre-Web Automated and audiobooks by Audiobookshelf.

### Platform changes these charts required

- `system/kubarr-worker` gained `batch/cronjobs` in its ClusterRole. Recyclarr is
  the first chart to ship a CronJob, and Helm reads every resource it owns before
  upgrading, so without it every Recyclarr upgrade would have failed on its own
  manifest. `tests/test_worker_can_manage_shipped_kinds.py` now guards the batch
  and RBAC kinds too, not only the policy kinds.
- `monitoring/victoriametrics` added `kube-state-metrics`, `vmalert`,
  `alertmanager`, `prowlarr`, `lidarr` and `navidrome` to
  `networkPolicy.scrapeNamespaces`, and `vmalert` to `networkPolicy.ingressFrom`.
  Target discovery is annotation-driven and needed no change; it is VictoriaMetrics'
  own default-deny egress and ingress allowlist that gate the traffic.
