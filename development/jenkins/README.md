# Jenkins

This chart runs one official Jenkins LTS 2.568.3 controller (JDK 21) at
`/jenkins/` through the Kubarr gateway. Its managed NFS `media-data` claim uses
the private `development/jenkins/jenkins/home` subdirectory for **all** of
`/var/jenkins_home`, including jobs, plugins, credentials and encryption keys.
The init container creates/chowns the path for UID/GID 1000 on Kubarr's
no-root-squash export. External root-squashed NFS requires server-side directory
ownership and permissions. Configure `storage.home.storageClassName` for a
dedicated PVC or select a pre-created PVC with `storage.home.existingClaim`;
ensure it is writable by UID/GID 1000.
Recreate upgrades stop the old controller before starting a replacement.

## First login

Wait for the controller pod to start, open `/jenkins/` via Kubarr, and retrieve
the one-time unlock password (treat the output as a secret):

```sh
kubectl -n jenkins exec deployment/jenkins -c jenkins -- cat /var/jenkins_home/secrets/initialAdminPassword
```

Complete the setup wizard and create an administrator account. No admin
password or setup-wizard bypass is shipped by this chart. The initial password
is also printed in pod logs on first startup. For a custom Helm release,
replace `deployment/jenkins` with its rendered Deployment name.

## Networking and agents

Only HTTP port 8080 is exposed internally to the `openresty` gateway namespace;
the shared NetworkPolicy allows DNS and public outbound traffic for the Jenkins
update center, plugins, and public Git hosts. Configure `networkPolicy.egressTo`
for private in-cluster Git or proxy namespaces. Other private-network Git hosts
need separately scoped egress rules/networking. Plugin installation needs
working DNS and access to `updates.jenkins.io` and its download mirrors.

Agents are configured **manually in Jenkins**, not provisioned by this chart.
Prefer inbound WebSocket agents (`-webSocket`) over HTTPS on the configured
Jenkins URL; no inbound TCP agent port 50000, Kubernetes plugin cloud, Docker
socket, or cluster RBAC is provided. The agent must be able to reach Jenkins
over a network path that supports WebSocket upgrades. Kubarr's gateway requires
Kubarr user authentication for application routes and does not automatically
authenticate non-browser clients: external webhooks, Git integrations, and
inbound agents may fail through it even when Jenkins credentials are valid.
For these integrations arrange a separately secured direct endpoint and
appropriate ingress/policy rules outside this chart; do not expose Jenkins
unauthenticated. Controller-initiated Git fetches can use public egress.

Set the Jenkins URL in **Manage Jenkins → System → Jenkins Location** to the
actual externally reachable URL ending in `/jenkins/`, with matching HTTPS and
forwarded host/protocol settings on the reverse proxy. The chart fixes
`JENKINS_OPTS=--prefix=/jenkins` to match the gateway's base-path annotation;
Jenkins redirects and agent URLs depend on that prefix.

## Backups

Back up the **entire** `/var/jenkins_home` PVC, including `secrets/`,
`credentials.xml`, job configuration and plugins, using a consistent snapshot
or with Jenkins stopped. Protect and test restores of backups; losing the
encryption keys makes stored credentials unusable. NFS is not a backup. To
migrate an old `jenkins-home` local PVC, stop the controller, back it up, copy
its entire contents (including hidden secrets) to `development/jenkins/jenkins/home`
preserving UID/GID 1000, verify the copy, then set
`storage.migrationConfirmed=true` on upgrade. The chart refuses to switch while
it detects the old PVC without the flag and retains the old PVC. Set
`storage.home.existingClaim=jenkins-home` to keep using it without migration.

References: [official Jenkins Docker image](https://github.com/jenkinsci/docker),
[setup wizard](https://www.jenkins.io/doc/book/installing/docker/#setup-wizard),
[Java support](https://www.jenkins.io/doc/book/platform-information/support-policy-java/).
