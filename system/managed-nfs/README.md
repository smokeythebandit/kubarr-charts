# Managed NFS

This chart hosts the shared NFSv4 export on a backing PVC in `kubarr-storage`.
The backing claim uses the bootstrap StorageClass, not its own exported storage.

## Server implementations

- `nfs.implementation: kernel` retains the legacy `nfs-server-alpine` server.
  It requires the host's kernel NFS-server (`nfsd`) support. The chart keeps this
  default for existing installations.
- `nfs.implementation: ganesha` runs NFS-Ganesha in userspace using the VFS FSAL.
  It does not require host `nfsd` support and supports the Talos installer path.
  The CLI selects this mode for fresh managed-NFS installations and preserves
  the implementation of existing releases.

Ganesha uses the separately pinned `ganesha.image` values. Its NFSv4 recovery
state is stored in `.kubarr-nfs-recovery` on the backing PVC. Keep this directory
when backing up or restoring the server. Both modes expose the configured
`nfs.exportPath` as the client export `/`, and support `nfs.readOnly` and
`nfs.permitted`. `nfs.sync` configures the legacy kernel server; Ganesha handles
NFS stable writes and commits through its VFS FSAL.

Do not switch an existing server's implementation while clients are mounted:
kernel and Ganesha file handles are not interchangeable. Plan a maintenance
window, backup, client unmount/remount, and verification for such a migration.
The CLI does not perform that migration automatically.

## Admission and readiness

The dedicated namespace declares Pod Security `privileged`, matching the server
pod's privileges and default host networking. Application namespaces are not
changed by this exception. The server uses TCP startup/readiness probes on port
2049, so a running container that failed to start NFS is not considered ready.
The installer observes Deployment readiness after Helm completes and surfaces
Pod Security/admission rejection without waiting for the full readiness timeout.
