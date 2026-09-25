# qBittorrent Helm Chart

This Helm chart deploys qBittorrent, a free and open-source BitTorrent client with a web interface.

## Prerequisites

- Kubernetes 1.34-1.37 when using Helm 4.3
- Helm 4 (tested with 4.3.0)
- PV provisioner support in the underlying infrastructure (for persistent storage)

## Installing the Chart

To install the chart with the release name `qbittorrent`:

```bash
helm install qbittorrent charts/qbittorrent -n media --create-namespace
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
| `qbittorrent.image.tag` | Image tag | `latest` |
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
| `qbittorrent.resources.requests.cpu` | CPU request | `200m` |
| `qbittorrent.resources.requests.memory` | Memory request | `512Mi` |
| `qbittorrent.resources.limits.cpu` | CPU limit | `2000m` |
| `qbittorrent.resources.limits.memory` | Memory limit | `2Gi` |

### Persistence

| Parameter | Description | Default |
|-----------|-------------|---------|
| `persistence.config.enabled` | Enable config persistence | `true` |
| `persistence.config.size` | Config volume size | `1Gi` |
| `persistence.config.storageClass` | Storage class | `""` (default) |
| `persistence.downloads.enabled` | Enable downloads persistence | `true` |
| `persistence.downloads.size` | Downloads volume size | `200Gi` |
| `persistence.downloads.storageClass` | Storage class | `""` (default) |

## Accessing qBittorrent

### Default Credentials

The default username and password for qBittorrent are:
- **Username**: `admin`
- **Password**: `adminadmin`

**IMPORTANT**: Change the default password immediately after first login!

### Port Forward (Local Access)

```bash
kubectl port-forward -n media svc/qbittorrent 8080:8080
```

Then access qBittorrent at: http://localhost:8080

## Upgrading

To upgrade the chart:

```bash
helm upgrade qbittorrent charts/qbittorrent -n media
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
kubectl get pods -n media -l app=qbittorrent
```

### View logs
```bash
kubectl logs -n media -l app=qbittorrent
```

### Describe pod
```bash
kubectl describe pod -n media -l app=qbittorrent
```

### Check PVC status
```bash
kubectl get pvc -n media
```

## License

This chart is provided as-is for use with Kubarr dashboard.
