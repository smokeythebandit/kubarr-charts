# Redmine

Redmine 7.0.1 (official Debian image) on port 3000, with PostgreSQL 16.15
bundled by default. The chart is in the `development` catalog category.

## Install

With bundled PostgreSQL, Helm generates separate strong credentials for the
database and Rails sessions. No pre-created Secret is needed:

```sh
helm dependency build --skip-refresh development/redmine
helm upgrade --install redmine development/redmine -n redmine --create-namespace
```

The generated `redmine-credentials` Secret is retained after uninstall and its
two keys are reused by live lookup on upgrades. Back it up securely with
`kubectl -n redmine get secret redmine-credentials -o yaml` directly into an
encrypted backup or secret manager (never a public log or repository). Upgrades
fail if the Secret is missing. On reinstall with a pre-existing shared claim
and no Secret, a pre-install Job checks Redmine's PostgreSQL subdirectory:
nonempty or unreadable data blocks new credentials, while an empty directory
allows a first install on an already-provisioned share. Restore the original
keys before reinstalling with an existing database. The hook must mount the
claim and read the directory as UID 999; failure blocks installation. Dedicated
existing database claims still require matching credentials or an explicitly
supplied `secrets.existingSecret`. For a PVC provisioned outside this chart,
set `storage.media.create=false` so Helm does not try to create the existing
PV/PVC again.
`secrets.existingSecret`, `secrets.databasePasswordKey`, and
`secrets.sessionKey` select an externally supplied Secret and keys instead
(no generation). External PostgreSQL **requires** this override.
An unchanged database password is essential
when reusing a PostgreSQL volume; changing `SECRET_KEY_BASE` invalidates sessions.
By default one managed NFS `media-data` claim holds **separate**
`development/redmine/redmine/postgres` and `development/redmine/redmine/files` directories;
these mount at `/var/lib/postgresql/data` and `/usr/src/redmine/files`.
Upgrade managed-nfs to `12.0.0+2` (`nfs.sync=true`) **before** deploying:
the chart rejects an existing async managed export. PostgreSQL requires hard
NFS mounts, reliable locks/fsync and a synchronous server; an external NFS
export must provide the same guarantees. Performance and recoverability differ
from local block storage; back up with `pg_dump` and test recovery. Root init
containers create/chown the directories for UID/GID 999 on the managed export.
With a root-squashed external export, preprovision these directories writable
by 999 on the server; fsGroup alone cannot override NFS permissions. Set
`database.bundled.persistence.storageClassName` or `.existingClaim` for a
dedicated database volume, and `storage.attachments.storageClassName` or
`.existingClaim` for a different attachments volume. Do not put both in one
directory.

For external PostgreSQL (version 14 or newer), set `database.bundled.enabled=false`,
`database.host`, `database.port`, `database.username`, `database.name`, and the
Secret reference. Provision the database and user separately. For private
in-cluster endpoints, add their namespace to `networkPolicy.egressTo`; for
external/private IPs ensure your CNI policy permits the database endpoint.
Bundled PostgreSQL is restricted to Redmine pods on TCP 5432. The shared
kubarr-common policy permits gateway ingress from the `openresty` namespace on
TCP 3000, DNS, public egress and configured namespace egress; these rules are
additive to any other cluster network policies.

**Immediately change the upstream `admin` / `admin` password** after first
login and review registration permissions. SMTP is optional: configure email
delivery in Redmine's `config/configuration.yml` using your own persistent
configuration/image customization and Secret-backed credentials; this chart does
not configure SMTP or store its password. Allow SMTP egress explicitly if needed.

The gateway forwards `/redmine` without stripping the prefix. The chart mounts
`config.ru` with a Rack `/redmine` map and sets `RAILS_RELATIVE_URL_ROOT` so
Rails generates prefixed links. The readiness probe checks `/redmine/login`.
Changing the prefix requires a coordinated gateway/Rack configuration change;
the chart rejects a mismatched `redmine.basePath`. Verify assets and plugin
links through the gateway after installation, especially with custom plugins.

## Backup and upgrade

Back up **both** the PostgreSQL database and the attachments tree as a consistent
pair, plus the credentials Secret and any custom configuration/plugins. For the
bundled database use `pg_dump` (or a consistent database snapshot), and snapshot
the attachments while writes are stopped; restore both before restarting the
application. Redmine's official entrypoint runs `db:migrate` on startup. The
Recreate strategy limits this release to one writer and prevents overlapping
migrations during rolling upgrades. Stop writes, back up, review Redmine release
notes and plugin compatibility before upgrading; test the restore and migrations.
PostgreSQL major upgrades require a deliberate `pg_upgrade` or dump/restore,
not simply changing the image tag. Retain the original Secret keys and claims.

**Upgrading a local-PVC installation to NFS needs a data migration.** Stop
Redmine and PostgreSQL writes, retain the old `redmine-files` and
`data-redmine-postgres-0` PVCs, back up both and the credentials Secret, and
restore database files (including ownership 999) and attachments to the NFS
subdirectories above; verify a consistent restore and only then set
`storage.migrationConfirmed=true`. The chart rejects an upgrade while it sees
an old PVC without this flag; it retains the old attachments PVC rather than
deleting it. Alternatively set explicit existing claims to continue using the
old volumes. Never switch an initialized database to an empty NFS directory.

Sources: [official Redmine image](https://hub.docker.com/_/redmine),
[Redmine installation/database matrix](https://www.redmine.org/projects/redmine/wiki/RedmineInstall),
[official PostgreSQL image](https://hub.docker.com/_/postgres).
See also [Redmine's sub-URI guide](https://www.redmine.org/projects/redmine/wiki/HowTo_Install_Redmine_in_a_sub-URI).
