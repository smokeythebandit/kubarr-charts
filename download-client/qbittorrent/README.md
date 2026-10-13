# qBittorrent Helm Chart

This Helm chart deploys qBittorrent, a free and open-source BitTorrent client with a web interface.

## Prerequisites

- Kubernetes 1.34-1.37 when using Helm 4.3
- Helm 4 (tested with 4.3.0)
- PV provisioner support in the underlying infrastructure (for persistent storage)

## Installing the Chart

To install the chart with the release name `qbittorrent`:

```bash
helm dependency build --skip-refresh download-client/qbittorrent
helm install qbittorrent download-client/qbittorrent -n qbittorrent --create-namespace --set namespace.create=false
```

## Uninstalling the Chart

To uninstall/delete the `qbittorrent` deployment:

```bash
helm uninstall qbittorrent -n media
```

## Configuration

The following table lists the configurable parameters of the qBittorrent chart and their default values.

### VPN sidecar

Set `vpn.enabled=true` and `vpn.secretName` to the name of the VPN credentials
Secret to start Gluetun v3.41.3 alongside qBittorrent. Gluetun's `/tmp/gluetun`
is a pod-local `emptyDir` initialized as root-only mode `0700`; it is recreated
on every pod replacement. Gluetun retains `NET_ADMIN` and `CHOWN` after dropping all
other Linux capabilities. The VPN requires a node with `/dev/net/tun` and a
security policy permitting this device and these capabilities.

`vpn.firewallOutboundSubnets` defaults to empty: no private network ranges
bypass the VPN. If the workload must reach a cluster-local DNS resolver or
another internal endpoint outside the tunnel, set this to the narrowest
cluster-specific CIDR(s), such as the DNS service IP `/32`. Such exceptions
route traffic outside the VPN; the cluster NetworkPolicy must also permit the
destination. Avoid allowing entire RFC1918 ranges.

With `vpn.portForwarding.enabled=true`, Gluetun POSTs the assigned `{{PORT}}`
to the local qBittorrent Web API, retrying for up to 30 attempts while the
WebUI starts. The callback uses `qbittorrent.service.targetPort` and can be
overridden with `vpn.portForwarding.upCommand`. Ensure the local WebUI allows
unauthenticated requests from `127.0.0.1` for this callback. No down callback
changes qBittorrent's listening port: the kill switch blocks traffic when the
VPN is down, and a future up callback replaces the stale port.

### Application Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `qbittorrent.replicaCount` | Number of replicas | `1` |
| `qbittorrent.image.repository` | Image repository | `linuxserver/qbittorrent` |
| `qbittorrent.image.tag` | Image tag | `5.2.3` |
| `qbittorrent.image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `qbittorrent.service.type` | Service type | `ClusterIP` |
| `qbittorrent.service.port` | Service port | `8080` |

### Environment Variables

| Parameter | Description | Default |
|-----------|-------------|---------|
| `qbittorrent.env.PUID` | User ID | `1000` |
| `qbittorrent.env.PGID` | Group ID | `1000` |
| `qbittorrent.env.TZ` | Timezone | `Etc/UTC` |
| `qbittorrent.env.WEBUI_PORT` | Web UI port | `8080` |

### Resource Limits

| Parameter | Description | Default |
|-----------|-------------|---------|
| `qbittorrent.resources.requests.cpu` | CPU request | `250m` |
| `qbittorrent.resources.requests.memory` | Memory request | `512Mi` |
| `qbittorrent.resources.limits.cpu` | CPU limit | unset |
| `qbittorrent.resources.limits.memory` | Memory limit | `2Gi` |

### Persistence

| Parameter | Description | Default |
|-----------|-------------|---------|
| `storage.config.create` | Create private config PVC | `true` |
| `storage.config.size` | Private config claim size | `5Gi` |
| `storage.config.storageClassName` | Config StorageClass (empty uses cluster default) | `""` |
| `storage.media.existingClaim` | Shared NFS claim | `media-data` |
| `storage.mounts` | Explicit NFS directories (default own downloads at `/downloads`) | See `values.yaml` |

For an installed release, first migrate old `config/qbittorrent` into the
private config claim and verify it; then follow
[the breaking storage migration](../../STORAGE-MIGRATION.md) before upgrading.

## Accessing qBittorrent

### Default Credentials

The default username is `admin`. qBittorrent generates a temporary Web UI
password on first boot; retrieve it from the container logs. No static
password is provided by this chart.

**IMPORTANT**: Change the default password immediately after first login!

### Port Forward (Local Access)

```bash
kubectl port-forward -n qbittorrent svc/qbittorrent 8080:8080
```

Then access qBittorrent at: http://localhost:8080

## Upgrading

To upgrade the chart:

```bash
helm upgrade qbittorrent download-client/qbittorrent -n qbittorrent
```
## Integration with Kubarr Dashboard

This chart is designed to work seamlessly with the Kubarr dashboard. The Kubarr dashboard can:
- Install qBittorrent via the app catalog
- Monitor qBittorrent's health and resource usage
- Restart qBittorrent pods
- Uninstall qBittorrent

The chart includes all necessary labels for Kubarr integration:
- `managed-by: kubarr`
- `category: download-client`
- `app: qbittorrent`

## Persistence

The chart mounts two persistent volumes:

1. **Config Volume** (`/config`): Stores qBittorrent configuration files
2. **Downloads Volume** (`/downloads`): Stores downloaded torrent files

Both volumes use PersistentVolumeClaims and will persist data across pod restarts and redeployments.

## Security Considerations

1. Change the default admin password immediately
2. Use strong passwords for the web UI
3. Consider network policies to restrict access
4. Keep the qBittorrent image updated

## Troubleshooting

### Check pod status
```bash
kubectl get pods -n qbittorrent -l app=qbittorrent
```

### View logs
```bash
kubectl logs -n qbittorrent -l app=qbittorrent
```

### Describe pod
```bash
kubectl describe pod -n qbittorrent -l app=qbittorrent
```

### Check PVC status
```bash
kubectl get pvc -n qbittorrent
```

## License

This chart is provided as-is for use with Kubarr dashboard.
