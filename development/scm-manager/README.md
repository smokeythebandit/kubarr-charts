# SCM-Manager

SCM-Manager 3.12.1 serves Git repositories over HTTP on port 8080. Kubarr
routes `/scm-manager` to the same context path in the application. Deploy in
the `scm-manager` namespace. By default it mounts Kubarr managed NFS's
`media-data` claim's private `development/scm-manager/scm-manager/home` subdirectory at
`/var/lib/scm` (SCM_HOME): repositories, users, configuration and plugins.
A root init container creates/chowns it for UID/GID 1000 on Kubarr's export;
root-squashed external exports need server-side directory permissions. The pod
uses UID/GID and fsGroup 1000. A dedicated claim can be selected with
`storage.home.storageClassName` or `storage.home.existingClaim`.

```sh
helm dependency build --skip-refresh development/scm-manager
helm upgrade --install scm-manager development/scm-manager --namespace scm-manager --create-namespace
kubectl logs -n scm-manager deployment/scm-manager
```

On first startup, find the **startup token for initial user creation** in the
pod logs, open `/scm-manager` through Kubarr and create the admin account using
that token. Store the chosen password privately; it cannot be recovered. For
unattended bootstrap, supply a pre-created Kubernetes Secret and set
`scm-manager.bootstrap.existingSecret` (key `initialPassword` by default) and
optionally `scm-manager.bootstrap.initialUser`. This sets
`SCM_WEBAPP_INITIALPASSWORD` only for account creation if no account exists; it
skips the plugin wizard. Do not put credentials in Helm values or Git. Remove
the bootstrap Secret reference after initial setup if no longer needed.

SCM-Manager advertises clone URLs relative to its configured `/scm-manager`
context. Git HTTP(S) cloning/pushing requires SCM-Manager credentials (such as
an SCM-Manager token). Kubarr gateway browser/session authorization is separate
and does not supply Git HTTP credentials: a headless `git clone` through the
Kubarr gateway may be blocked by gateway authentication. Use a deliberately
configured authenticated external reverse proxy or other approved route for
headless Git clients; preserve the path and forward `X-Forwarded-Host`,
`X-Forwarded-For`, and `X-Forwarded-Proto`, and avoid encoding slashes. The
chart enables SCM-Manager's forwarded-header processing. No SSH plugin port is
exposed; SSH cloning needs a separately configured plugin, listener and
network access, which this chart does not provide.

Back up the entire `/var/lib/scm` claim, including repository data, metadata,
configuration and plugins. Quiesce writes (scale the Deployment to zero) before
filesystem backups, or use a storage snapshot with consistency guarantees.
Keep an off-cluster copy and test restoration to a new claim. Restore the claim
before restarting SCM-Manager; do not run two writers on the same data. Recreate
rollouts intentionally stop the old instance before starting a new one. A
managed NFS claim is retained on uninstall; retain a backup too. To migrate an
old `scm-manager-home` local claim, stop writes, back it up, copy the entire
claim to `development/scm-manager/scm-manager/home` preserving UID/GID 1000, verify the
restore, then set `storage.migrationConfirmed=true`. The chart refuses to switch
while it detects the old PVC without this flag and retains the PVC. Set
`storage.home.existingClaim` to keep using the old claim instead.

References: [official Docker deployment](https://scm-manager.org/docs/3.12.x/en/installation/docker/),
[first startup](https://scm-manager.org/docs/3.12.x/en/first-startup/),
[reverse proxy](https://scm-manager.org/docs/3.12.x/en/administration/reverse-proxies/).
