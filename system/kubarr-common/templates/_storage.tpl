{{/* Shared NFS-backed storage helpers. Every NFS container declares its mounts. */}}

{{- define "kubarr-common.storage.configMount" -}}
{{- $mountPath := default "/config" .mountPath -}}
- name: config
  mountPath: {{ $mountPath }}
  {{- if .readOnly }}
  readOnly: true
  {{- end }}
{{- end }}

{{/* All NFS data mounts are explicit; pass a list for non-primary workloads. */}}
{{- define "kubarr-common.storage.appMounts" -}}
{{- $mounts := .root.Values.storage.mounts -}}
{{- if hasKey . "mounts" -}}{{- $mounts = .mounts -}}{{- end -}}
{{- include "kubarr-common.storage.validateMounts" (dict "root" .root "mounts" $mounts "reservedPaths" (default (list "/config") .reservedPaths) "allowRoot" (default false .allowRoot)) -}}
{{- range $mount := $mounts }}
- name: media
  mountPath: {{ $mount.target | quote }}
  {{- if ne $mount.source "." }}
  subPath: {{ $mount.source | quote }}
  {{- end }}
  {{- if eq $mount.mode "read" }}
  readOnly: true
  {{- end }}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.mediaVolume" -}}
{{- $root := .root -}}
- name: media
  persistentVolumeClaim:
    claimName: {{ include "kubarr-common.storage.mediaClaimName" . }}
{{- end }}

{{/* Transcoding scratch. A transcoder writes segments at the full output
     bitrate and regenerates whatever it loses, so the shared export is the
     wrong home: one 4K session can exceed 100 GB, and every segment crosses
     the network three times (written, read back, delivered). Default it to the
     node and require a cap, so an unbounded session evicts the pod rather than
     filling the node and taking its neighbours with it. backend=nfs restores
     the shared-export placement for a node with no room to spare. */}}
{{- define "kubarr-common.storage.scratchBackend" -}}
{{- $storage := default dict .root.Values.storage -}}
{{- if hasKey $storage "scratch" -}}
{{- default "node" (default dict $storage.scratch).backend -}}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.validateScratch" -}}
{{- $storage := default dict .root.Values.storage -}}
{{- if hasKey $storage "scratch" -}}
{{- $scratch := $storage.scratch -}}
{{- if not (kindIs "map" $scratch) -}}{{- fail "storage.scratch must be an object" -}}{{- end -}}
{{- range $key, $_ := $scratch -}}
  {{- if not (has $key (list "backend" "target" "sizeLimit" "source")) -}}{{- fail (printf "storage.scratch.%s is not supported" $key) -}}{{- end -}}
{{- end -}}
{{- $backend := default "node" $scratch.backend -}}
{{- if not (has $backend (list "node" "nfs")) -}}{{- fail "storage.scratch.backend must be node or nfs" -}}{{- end -}}
{{- if not (regexMatch "^/([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+$" (toString (default "" $scratch.target))) -}}{{- fail "storage.scratch.target must be an absolute path without traversal" -}}{{- end -}}
{{- if eq $scratch.target "/config" -}}{{- fail "storage.scratch.target must not shadow the private config mount" -}}{{- end -}}
{{- if eq $backend "node" -}}
  {{- if hasKey $scratch "source" -}}{{- fail "storage.scratch.source applies only when backend=nfs" -}}{{- end -}}
  {{- if not (regexMatch "^[1-9][0-9]*(\\.[0-9]+)?(Ki|Mi|Gi|Ti)$" (toString (default "" $scratch.sizeLimit))) -}}{{- fail "storage.scratch.sizeLimit must be a positive binary quantity when backend=node" -}}{{- end -}}
{{- else -}}
  {{- if hasKey $scratch "sizeLimit" -}}{{- fail "storage.scratch.sizeLimit applies only when backend=node" -}}{{- end -}}
  {{- if not (regexMatch "^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$" (toString (default "" $scratch.source))) -}}{{- fail "storage.scratch.source must be a safe relative subpath when backend=nfs" -}}{{- end -}}
  {{- if or (eq $scratch.source "config") (hasPrefix "config/" $scratch.source) -}}{{- fail "storage.scratch.source exposes the former shared config root" -}}{{- end -}}
  {{- if not $storage.media.existingClaim -}}{{- fail "storage.media.existingClaim is required when storage.scratch.backend=nfs" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.scratchMount" -}}
{{- include "kubarr-common.storage.validateScratch" . -}}
{{- $storage := default dict .root.Values.storage -}}
{{- if hasKey $storage "scratch" -}}
{{- $scratch := $storage.scratch -}}
{{- if eq (default "node" $scratch.backend) "node" }}
- name: scratch
  mountPath: {{ $scratch.target | quote }}
{{- else }}
- name: media
  mountPath: {{ $scratch.target | quote }}
  subPath: {{ $scratch.source | quote }}
{{- end -}}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.scratchVolume" -}}
{{- include "kubarr-common.storage.validateScratch" . -}}
{{- $storage := default dict .root.Values.storage -}}
{{- if eq (include "kubarr-common.storage.scratchBackend" .) "node" }}
- name: scratch
  emptyDir:
    sizeLimit: {{ $storage.scratch.sizeLimit | quote }}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.mediaClaimName" -}}
{{- .root.Values.storage.media.existingClaim -}}
{{- end }}

{{/* Kubelet creates subPath directories as root, which a normal export
     squashes. Create them first as the application's own uid in the media
     group, so neither a chown nor a privileged step is involved. A "." source
     mounts the export root, which already exists. Read-only sources are
     created too (kubelet cannot create them either) but, listed after
     --read-only, need not be writable: a library may belong to another user. */}}
{{- define "kubarr-common.storage.prepareMounts" -}}
{{- $mounts := .root.Values.storage.mounts -}}
{{- if hasKey . "mounts" -}}{{- $mounts = .mounts -}}{{- end -}}
{{- $uid := int (default 1000 .uid) -}}
{{- $gid := int (default 1000 .gid) -}}
{{- $writable := list -}}
{{- $readOnly := list -}}
{{- range $mount := $mounts -}}
{{- if ne $mount.source "." -}}
{{- if eq $mount.mode "read-write" -}}
{{- $writable = append $writable $mount.source -}}
{{- else -}}
{{- $readOnly = append $readOnly $mount.source -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{/* A shared-export scratch directory needs the same preparation. */}}
{{- if eq (include "kubarr-common.storage.scratchBackend" (dict "root" .root)) "nfs" -}}
{{- $writable = append $writable .root.Values.storage.scratch.source -}}
{{- end -}}
{{- $writable = uniq $writable -}}
{{- $readOnly = without (uniq $readOnly) $writable -}}
{{- if or $writable $readOnly -}}
- name: prepare-media-directories
  image: {{ default "busybox:1.37.0" .root.Values.storage.prepareImage }}
  command: [sh, -ec, 'umask 0002; writable=1; for directory do if [ "$directory" = --read-only ]; then writable=; continue; fi; mkdir -p -- "$directory"; if [ -n "$writable" ]; then test -w "$directory"; fi; done', --]
  args:
    {{- range $source := $writable }}
    - {{ printf "/nfs/%s" $source | quote }}
    {{- end }}
    {{- if $readOnly }}
    - "--read-only"
    {{- range $source := $readOnly }}
    - {{ printf "/nfs/%s" $source | quote }}
    {{- end }}
    {{- end }}
  securityContext:
    runAsUser: {{ $uid }}
    runAsGroup: {{ $gid }}
    runAsNonRoot: {{ ne $uid 0 }}
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: [ALL]
  volumeMounts:
    - name: media
      mountPath: /nfs
{{- end -}}
{{- end }}

{{/* The shared export is writable by the media group. An application whose uid
     is not 1000 needs its membership to reach prepared directories. */}}
{{- define "kubarr-common.storage.mediaGroup" -}}
supplementalGroups: [1000]
{{- end }}

{{- define "kubarr-common.storage.mediaClaim" -}}
{{- $root := .root -}}
{{- include "kubarr-common.storage.deprecated" $root -}}
{{- $storage := default dict $root.Values.storage -}}
{{- if or (not (hasKey $storage "mounts")) (not (kindIs "slice" $storage.mounts)) -}}{{- fail "storage.mounts must be a list, even when empty" -}}{{- end -}}
{{- $media := default dict $storage.media -}}
{{- if or (not (kindIs "string" $media.existingClaim)) (gt (len $media.existingClaim) 63) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $media.existingClaim)) -}}{{- fail "storage.media.existingClaim must be a valid DNS claim name" -}}{{- end -}}
{{- if and (hasKey $media "create") (not (kindIs "bool" $media.create)) -}}{{- fail "storage.media.create must be a boolean" -}}{{- end -}}
{{- if and (hasKey $media "nfs") (not (kindIs "map" $media.nfs)) -}}{{- fail "storage.media.nfs must be an object" -}}{{- end -}}
{{- $nfs := default dict $media.nfs -}}
{{- if hasKey $nfs "server" -}}
  {{- if or (not (kindIs "string" $nfs.server)) (not (or (regexMatch "^[A-Za-z0-9][A-Za-z0-9.:-]*$" $nfs.server) (regexMatch "^\\[[0-9A-Fa-f:.]+\\]$" $nfs.server))) -}}{{- fail "storage.media.nfs.server must be a nonempty hostname or IP address" -}}{{- end -}}
{{- end -}}
{{- if hasKey $nfs "path" -}}
  {{- if or (not (kindIs "string" $nfs.path)) (not (regexMatch "^/([A-Za-z0-9._-]+(/?[A-Za-z0-9._-]+)*)?$" $nfs.path)) -}}{{- fail "storage.media.nfs.path must be an absolute NFS export path" -}}{{- end -}}
  {{- range $segment := splitList "/" $nfs.path -}}{{- if has $segment (list "." "..") -}}{{- fail "storage.media.nfs.path must not contain dot traversal" -}}{{- end -}}{{- end -}}
{{- end -}}
{{- if hasKey $nfs "size" -}}
  {{- if or (not (kindIs "string" $nfs.size)) (not (regexMatch "^[1-9][0-9]*(\\.[0-9]+)?(Ki|Mi|Gi|Ti|Pi|Ei|[kMGTPE])?$" $nfs.size)) -}}{{- fail "storage.media.nfs.size must be a positive Kubernetes storage quantity" -}}{{- end -}}
{{- end -}}
{{- $create := true -}}
{{- if hasKey $media "create" -}}
{{- $create = $media.create -}}
{{- end -}}
{{- if not $create -}}{{- include "kubarr-common.storage.assertRetainedMedia" $root -}}{{- end -}}
{{- if $create }}
{{- $namespace := default $root.Release.Namespace $root.Values.namespace.name -}}
{{- $claimName := $media.existingClaim -}}
{{- $pvName := printf "kubarr-media-%s-%s" $namespace $claimName | trunc 63 | trimSuffix "-" -}}
{{- $defaultNfsServer := "kubarr-managed-nfs.kubarr-storage.svc.cluster.local" -}}
{{- $managedNfsService := lookup "v1" "Service" "kubarr-storage" "kubarr-managed-nfs" -}}
{{- if and $managedNfsService $managedNfsService.spec.clusterIP -}}
{{- $defaultNfsServer = $managedNfsService.spec.clusterIP -}}
{{- end -}}
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {{ $pvName }}
  annotations:
    helm.sh/resource-policy: keep
spec:
  capacity:
    storage: {{ default "1Ti" $nfs.size }}
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  nfs:
    server: {{ default $defaultNfsServer $nfs.server | quote }}
    path: {{ default "/" $nfs.path | quote }}
  claimRef:
    namespace: {{ $namespace }}
    name: {{ $claimName }}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ $claimName }}
  namespace: {{ $namespace }}
  annotations:
    helm.sh/resource-policy: keep
spec:
  volumeName: {{ $pvName }}
  storageClassName: ""
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: {{ default "1Ti" $nfs.size }}
{{- end }}
{{- end }}

{{/* Run whenever a Helm-managed share stops rendering (including a backend change). */}}
{{- define "kubarr-common.storage.assertRetainedMedia" -}}
{{- $root := . -}}
{{- if $root.Release.IsUpgrade -}}
  {{- $namespace := default $root.Release.Namespace $root.Values.namespace.name -}}
  {{- $claimName := $root.Values.storage.media.existingClaim -}}
  {{- $pvName := printf "kubarr-media-%s-%s" $namespace $claimName | trunc 63 | trimSuffix "-" -}}
  {{- range $object := list (dict "kind" "PersistentVolumeClaim" "namespace" $namespace "name" $claimName) (dict "kind" "PersistentVolume" "namespace" "" "name" $pvName) -}}
    {{- $live := lookup "v1" $object.kind $object.namespace $object.name -}}
    {{- if $live -}}
      {{- $annotations := default dict $live.metadata.annotations -}}
      {{- if and (eq (default "" (index $annotations "meta.helm.sh/release-name")) $root.Release.Name) (eq (default "" (index $annotations "meta.helm.sh/release-namespace")) $root.Release.Namespace) (ne (default "" (index $annotations "helm.sh/resource-policy")) "keep") -}}
        {{- fail (printf "storage.media.create=false would remove Helm-owned %s %s without keep; first upgrade with create=true to mark it keep, then switch to an externally owned claim (see STORAGE-MIGRATION.md)" $object.kind $object.name) -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.configOrMediaVolume" -}}
{{- $mounts := .root.Values.storage.mounts -}}
{{- if or (gt (len $mounts) 0) (eq (include "kubarr-common.storage.scratchBackend" .) "nfs") }}
{{ include "kubarr-common.storage.mediaVolume" . }}
{{- end }}
{{- include "kubarr-common.storage.scratchVolume" . }}
{{ include "kubarr-common.storage.configVolume" . }}
{{- end }}

{{- define "kubarr-common.storage.configVolume" -}}
- name: config
  persistentVolumeClaim:
    claimName: {{ include "kubarr-common.storage.configClaimName" . | quote }}
{{- end }}

{{/* Private config is an RWO PVC using the cluster default StorageClass unless
     an explicit class or pre-provisioned claim is selected. Empty class != NFS. */}}
{{- define "kubarr-common.storage.configClaimName" -}}
{{- $fullname := .fullname -}}
{{- $generated := printf "%s-config" $fullname -}}
{{- if gt (len $generated) 63 -}}
  {{- $generated = printf "%s-%s-config" ($fullname | trunc 47 | trimSuffix "-") (sha256sum $fullname | trunc 8) -}}
{{- end -}}
{{- default $generated .root.Values.storage.config.existingClaim -}}
{{- end }}

{{- define "kubarr-common.storage.configClaim" -}}
{{- if .root.Values.storage.config.create -}}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "kubarr-common.storage.configClaimName" . | quote }}
  namespace: {{ .root.Values.namespace.name }}
  annotations:
    helm.sh/resource-policy: keep
spec:
  accessModes: [ReadWriteOnce]
  {{- with .root.Values.storage.config.storageClassName }}
  storageClassName: {{ . | quote }}
  {{- end }}
  resources:
    requests:
      storage: {{ .root.Values.storage.config.size }}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.deprecated" -}}
{{- $s := default dict .Values.storage -}}
{{- range $key := list "layout" "profile" "access" "readOnly" "mountEnabled" "mountPath" -}}
  {{- if hasKey $s $key -}}{{- fail (printf "storage.%s was removed; see the declarative storage migration in README.md; remove old values before upgrading" $key) -}}{{- end -}}
{{- end -}}
{{- $media := default dict $s.media -}}
{{- range $key := list "mountPath" "configMountPath" "configSubPath" "downloadsMountPath" "downloadsSubPath" "mediaMountPath" "mediaSubPath" "dataSubPath" "transcodeMountPath" "transcodeSubPath" "cacheMountPath" "cacheSubPath" "subPath" -}}
  {{- if hasKey $media $key -}}{{- fail (printf "storage.media.%s was removed; declare storage.mounts instead (see README.md)" $key) -}}{{- end -}}
{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.validateMounts" -}}
{{- $mounts := .mounts -}}
{{- $label := default "storage.mounts" .label -}}
{{- if not (kindIs "slice" $mounts) -}}{{- fail (printf "%s must be a list" $label) -}}{{- end -}}
{{- if and (gt (len $mounts) 0) (not .root.Values.storage.media.existingClaim) -}}{{- fail "storage.media.existingClaim is required for NFS mounts" -}}{{- end -}}
{{- include "kubarr-common.storage.validateScratch" (dict "root" .root) -}}
{{- $names := list -}}{{- $targets := default list .reservedPaths -}}{{- $sources := list -}}{{- $modes := list -}}
{{/* Scratch is mounted outside this list, so reserve its path here rather than
     letting a shared mount shadow it. */}}
{{- $scratch := default dict (default dict .root.Values.storage).scratch -}}
{{- with (default "" $scratch.target) -}}{{- $targets = append $targets . -}}{{- end -}}
{{- range $i, $m := $mounts -}}
  {{- $at := printf "%s[%d]" $label $i -}}
  {{- if not (kindIs "map" $m) -}}{{- fail (printf "%s must be an object" $at) -}}{{- end -}}
  {{- range $key := list "name" "source" "target" "mode" -}}
    {{- if or (not (hasKey $m $key)) (not (kindIs "string" (index $m $key))) (eq (index $m $key) "") -}}{{- fail (printf "%s.%s must be a nonempty string" $at $key) -}}{{- end -}}
  {{- end -}}
  {{- range $key, $_ := $m -}}{{- if not (has $key (list "name" "source" "target" "mode")) -}}{{- fail (printf "%s.%s is not supported" $at $key) -}}{{- end -}}{{- end -}}
  {{- if or (gt (len $m.name) 63) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $m.name)) (has $m.name $names) -}}{{- fail (printf "%s.name must be a unique DNS label" $at) -}}{{- end -}}
  {{- $names = append $names $m.name -}}
  {{- if not (or (and $.allowRoot (eq $m.source ".")) (regexMatch "^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$" $m.source)) -}}{{- fail (printf "%s.source must be a safe relative subpath" $at) -}}{{- end -}}
  {{- if or (eq $m.source "config") (hasPrefix "config/" $m.source) -}}{{- fail (printf "%s.source exposes the former shared config root" $at) -}}{{- end -}}
  {{- if not (regexMatch "^/([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+$" $m.target) -}}{{- fail (printf "%s.target must be an absolute path without traversal" $at) -}}{{- end -}}
  {{- if not (has $m.mode (list "read" "read-write")) -}}{{- fail (printf "%s.mode must be read or read-write" $at) -}}{{- end -}}
  {{- range $path := $targets -}}
    {{- if or (eq $path $m.target) (hasPrefix (printf "%s/" $path) $m.target) (hasPrefix (printf "%s/" $m.target) $path) -}}{{- fail (printf "%s.target overlaps %s" $at $path) -}}{{- end -}}
  {{- end -}}
  {{- range $j, $source := $sources -}}
    {{- if and (ne $m.mode (index $modes $j)) (or (eq $source $m.source) (hasPrefix (printf "%s/" $source) $m.source) (hasPrefix (printf "%s/" $m.source) $source)) -}}{{- fail (printf "%s.source overlaps a mount with different mode" $at) -}}{{- end -}}
  {{- end -}}
  {{- $targets = append $targets $m.target -}}{{- $sources = append $sources $m.source -}}{{- $modes = append $modes $m.mode -}}
{{- end -}}
{{- end }}

{{/* Persistent application state must never silently fall back to the image filesystem.
     Returns the source used by bootstrap init containers, independent of list order. */}}
{{- define "kubarr-common.storage.requiredSource" -}}
{{- $label := default "storage.mounts" .label -}}
{{- include "kubarr-common.storage.validateMounts" (dict "root" .root "mounts" .mounts "label" $label "reservedPaths" (default list .reservedPaths)) -}}
{{- $source := "" -}}
{{- range $mount := .mounts -}}
  {{- if eq $mount.target $.target -}}
    {{- if ne $mount.mode "read-write" -}}{{- fail (printf "%s requires a writable NFS mount at %s" $label $.target) -}}{{- end -}}
    {{- $source = $mount.source -}}
  {{- end -}}
{{- end -}}
{{- if not $source -}}{{- fail (printf "%s requires a writable NFS mount at %s" $label .target) -}}{{- end -}}
{{- $source -}}
{{- end }}

{{- define "kubarr-common.storage.validateConfig" -}}
{{- include "kubarr-common.storage.deprecated" .root -}}
{{- $s := .root.Values.storage -}}
{{- if and (hasKey . "replicas") (ne (int .replicas) 1) -}}{{- fail "private config requires a single app replica" -}}{{- end -}}
{{- if hasKey $s "migrationConfirmed" -}}{{- fail "storage.migrationConfirmed was removed; use storage.config.migrationConfirmed after private-config migration" -}}{{- end -}}
{{- include "kubarr-common.storage.validateMounts" (dict "root" .root "mounts" $s.mounts "reservedPaths" (default (list "/config") .reservedPaths)) -}}
{{- if and $s.config.create $s.config.existingClaim -}}{{- fail "storage.config: set create=false when using existingClaim" -}}{{- end -}}
{{- if and (not $s.config.create) (not $s.config.existingClaim) -}}{{- fail "storage.config.existingClaim is required when create=false" -}}{{- end -}}
{{- if and (not $s.config.create) $s.config.storageClassName -}}{{- fail "storage.config.storageClassName applies only when create=true" -}}{{- end -}}
{{- if and $s.config.create (not (regexMatch "^[1-9][0-9]*(\\.[0-9]+)?(Ki|Mi|Gi|Ti|Pi|Ei)$" (toString $s.config.size))) -}}{{- fail "storage.config.size must be a positive binary quantity" -}}{{- end -}}
{{- $claim := include "kubarr-common.storage.configClaimName" . -}}
{{- if or (gt (len $claim) 63) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $claim)) (eq $claim $s.media.existingClaim) -}}{{- fail "storage.config claim must be a distinct valid DNS label" -}}{{- end -}}
{{- if and .root.Release.IsUpgrade (not $s.config.migrationConfirmed) -}}{{- fail "private config upgrade requires storage.config.migrationConfirmed=true after copying and verifying legacy config data; see README.md" -}}{{- end -}}
{{- end }}

{{- define "kubarr-common.storage.hasDownloadClient" -}}
{{- range $m := .mounts -}}
  {{- if and (eq $m.source (printf "downloads/%s" $.client)) (eq $m.target (printf "/downloads/%s" $.client)) (eq $m.mode "read-write") -}}true{{- end -}}
{{- end -}}
{{- end }}
