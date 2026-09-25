# Kubarr Charts

Helm charts for Kubarr and the applications it manages. Charts are grouped under
`download-client/`, `indexer/`, `media-manager/`, `media-server/`, `monitoring/`,
and `system/`. There is no umbrella `kubarr` chart; use the Kubarr CLI to bootstrap
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

## Deployment Notes

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
