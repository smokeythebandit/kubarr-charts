# Headlamp

Optional monitoring catalog app at `/headlamp/` through Kubarr's authenticated
OpenResty gateway. Headlamp uses its own **separate** Kubernetes bearer-token
login: signing in to Kubarr does not grant Kubernetes API permissions. The
Service annotation `kubarr.io/base-path: /headlamp` makes the gateway preserve
the prefix, matching Headlamp's `-base-url=/headlamp` and `/headlamp/` probes.
Use the gateway's HTTPS endpoint to enter tokens.

The pod uses a dedicated, unbound ServiceAccount token for upstream in-cluster
cluster discovery. No ClusterRoleBinding, cluster-admin access, or shared
service-account login is installed. Each user must supply their own token;
Kubernetes RBAC determines what that user can see and change. For example,
an administrator can provision a **read-only** login identity:

```sh
kubectl -n headlamp create serviceaccount headlamp-viewer
kubectl create clusterrolebinding headlamp-viewer --clusterrole=view --serviceaccount=headlamp:headlamp-viewer
kubectl -n headlamp create token headlamp-viewer --duration=1h
```

Paste the short-lived token into Headlamp's login form; do not embed it in
Helm values or store it in a permanent Secret. `view` is a cluster-wide
read-only example; for narrower access bind a suitable Role in the desired
namespace instead. TokenRequest duration may be capped by the API server.
Headlamp's backend still requires connectivity to the Kubernetes API server;
restrict `networkPolicy.apiServerCIDRs` and `apiServerPorts` to your API
endpoints if known. DNS is allowed for service discovery.

The image listens on HTTP port 4466. The root filesystem is read-only;
an ephemeral `/tmp` volume supports runtime temporary files. No PVC or
permanent token storage is required. Session/browser state is managed by
Headlamp; users may need to sign in again when their token expires.

The icon mark is extracted (cropped from the wordmark) from upstream
[`docs/headlamp_light.svg` at v0.45.0](https://github.com/kubernetes-sigs/headlamp/blob/v0.45.0/docs/headlamp_light.svg),
licensed [Apache-2.0](https://github.com/kubernetes-sigs/headlamp/blob/v0.45.0/LICENSE).
