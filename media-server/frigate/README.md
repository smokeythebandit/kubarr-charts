# Frigate on Kubarr

This chart runs the official `ghcr.io/blakeblackshear/frigate:0.18.0` image with
one replica. Kubarr discovers its `frigate` ClusterIP Service on **8971** and
proxies it through OpenResty. The seed configuration disables Frigate's TLS on
8971 so Kubarr's HTTP upstream works; Frigate's own login **remains enabled**.
Kubarr gateway authentication and Frigate accounts are separate: on first boot,
retrieve the generated Frigate admin password from the Frigate container logs,
log in through Kubarr, then change the password under Settings → Users. Do not
disable Frigate authentication unless you have explicitly configured a trusted
proxy with appropriate Frigate proxy authentication. The unauthenticated port
**5000 gives admin-level access**: it is not exposed by the Service and ingress
policy permits only OpenResty on 8971. Avoid port-forwarding directly to 5000.

## Cameras and configuration

The first startup copies the minimal `config` value from `values.yaml` into
`/config/config.yml` **only if neither `config.yml` nor `config.yaml` exists**.
It provides a disabled dummy camera and disabled MQTT. In the Frigate UI use
Settings → Camera configuration to add real cameras, or edit `config.yml` on
the local config PVC. Changes made in Frigate survive pod restarts and chart
upgrades. Updating Helm `config` after first boot does **not** update the live
file; modify the persistent configuration instead. Back up the config PVC and
SQLite database before Frigate upgrades.

An example camera entry for the persisted config (adapt the URL and stream):

```yaml
cameras:
  front_door:
    ffmpeg:
      inputs:
        - path: rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@192.168.1.30:554/stream
          roles: [detect, record]
record:
  enabled: true
```

Store credentials in a pre-created Kubernetes Secret in the **frigate**
namespace with keys such as `FRIGATE_RTSP_USER` and `FRIGATE_RTSP_PASSWORD`,
then set `frigate.existingSecret` to its name. Frigate substitutes `FRIGATE_*`
variables only in supported config fields. Do not put plaintext passwords in
Helm values or the seed ConfigMap. MQTT is optional: to use an external broker,
edit the persisted config to set `mqtt.enabled: true`, `mqtt.host` (and optional
`mqtt.user: "{FRIGATE_MQTT_USER}"` / `mqtt.password: "{FRIGATE_MQTT_PASSWORD}"`),
and supply the credentials via the same Secret. This chart does not install an
MQTT broker. When targeting private camera/broker IPs, add their CIDRs to
`networkPolicy.privateEgressCIDRs` (for example `192.168.1.0/24`); the common
policy otherwise blocks private network egress. Set DNS/namespace egress rules
as needed for brokers hosted inside the cluster.

## Storage and resources

`/config` (configuration, JWT secret and SQLite database/WAL files) uses a
separate **local/block** ReadWriteOnce PVC (cluster default StorageClass). Verify
the default is local/block or set `storage.config.storageClassName`. Do not use
NFS or the shared media PVC for SQLite; use `storage.config.existingClaim` for
an already-provisioned local/block PVC. If using a local-path provisioner,
ensure the node holding the PVC remains available. Recreate deployment strategy
avoids concurrent database writers. `/media/frigate` (recordings, clips and
exports) mounts the writable `frigate` subdirectory of the shared
`media-data` claim. By default the shared storage helper creates a per-namespace
NFS PV/PVC; configure `storage.media.nfs.server` and `.path` for external NFS.
For a pre-existing claim, set `storage.media.create=false` and specify
`storage.media.existingClaim` and `.subPath` as needed.
Ensure that directory is writable by the container on your NFS server. Frigate
manages the recordings directory layout; do not rearrange it manually. Shared
media availability and capacity affect recordings.

Frigate 0.18's database is a Peewee SQLite queue database using WAL; [SQLite
WAL is not safe on a network filesystem](https://sqlite.org/wal.html#overview).
Frigate has no supported external PostgreSQL backend or chart switch to
rollback journaling. `/config` (which contains the SQLite DB) is therefore an
explicit exception to NFS-only persistence. Back up the database consistently
with Frigate stopped; never copy a live SQLite database onto NFS.

`/dev/shm` is RAM-backed (128Mi default); increase `storage.shmSize` for more
cameras or higher detect resolution, accounting for Frigate logs. `/tmp/cache`
is a RAM-backed 1Gi staging directory for recording segments; adjust
`storage.cacheSize` and pod memory resources together for your camera load.
The probes use HTTP `/` on 8971 with TLS disabled in the seeded config. If you
enable Frigate TLS later, adapt the probes and Kubarr upstream accordingly.

## Accelerators

No host devices, privileged mode or GPU are enabled by default. `gpu.enabled`
allocates a Kubernetes device-plugin resource using kubarr-common; select
`gpu.provider` (`intel`, `nvidia`, `amd`), optional `gpu.resourceName`,
`gpu.runtimeClassName` and numeric `gpu.supplementalGroups` as needed. AMD
requires a device-plugin resource name. A resource allocation alone does not
configure decoding/detection: set `ffmpeg.hwaccel_args` (e.g. `preset-vaapi`
for Intel/AMD or `preset-nvidia` for NVIDIA) and any detector separately in
the persistent Frigate config. NVIDIA TensorRT/ROCm, Coral and platform-specific
devices can require different Frigate images and additional cluster-side
device-plugin setup; verify compatibility before switching image tags.

References: [installation/storage/shm/ports](https://docs.frigate.video/frigate/installation/),
[config and secrets](https://docs.frigate.video/configuration/config/),
[authentication](https://docs.frigate.video/configuration/authentication/),
[TLS](https://docs.frigate.video/configuration/tls/),
[video acceleration](https://docs.frigate.video/configuration/hardware_acceleration_video/),
[0.18.0 release and image tags](https://github.com/blakeblackshear/frigate/releases/tag/v0.18.0).
