{{- define "unpackerr.name" -}}{{ include "kubarr-common.name" . }}{{- end }}
{{- define "unpackerr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "unpackerr.chart" -}}{{ include "kubarr-common.chart" . }}{{- end }}
{{- define "unpackerr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "unpackerr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "unpackerr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}

{{/* The download folders to watch: every mount under /downloads. */}}
{{- define "unpackerr.folders" -}}
{{- $folders := list -}}
{{- range $mount := .Values.storage.mounts -}}
{{- if and (hasPrefix "/downloads/" $mount.target) (ne $mount.mode "read") -}}
{{- $folders = append $folders $mount -}}
{{- end -}}
{{- end -}}
{{- toJson $folders -}}
{{- end }}

{{/* Folder watching extracts every archive in the download folders. With the
     Starr link on, Unpackerr follows Sonarr, Radarr and Lidarr's queues instead,
     and watching the same folders as well would extract twice, so "auto" turns
     folder watching off whenever the link is on. */}}
{{- define "unpackerr.watchFolders" -}}
{{- $mode := toString .Values.unpackerr.folderWatch -}}
{{- if not (has $mode (list "auto" "true" "false")) -}}
{{- fail "unpackerr.folderWatch must be auto, true or false" -}}
{{- end -}}
{{- if or (eq $mode "true") (and (eq $mode "auto") (not (default dict .Values.starrLink).enabled)) -}}true{{- end -}}
{{- end }}

{{/* Unpackerr instance keys must be simple identifiers. */}}
{{- define "unpackerr.folderKey" -}}
{{- regexReplaceAll "[^a-z0-9]" (lower .) "_" -}}
{{- end }}

{{- define "unpackerr.validate" -}}
{{- if ne (int .Values.unpackerr.replicaCount) 1 -}}
{{- fail "unpackerr.replicaCount must be 1: two pollers would extract the same archive twice" -}}
{{- end -}}
{{- range $key := list "pollInterval" "deleteExtractedAfter" -}}
{{- if not (regexMatch "^[0-9]+(ms|s|m|h)$" (toString (index $.Values.unpackerr $key))) -}}
{{- fail (printf "unpackerr.%s must be a Go duration such as 30s, 1m or 2h" $key) -}}
{{- end -}}
{{- end -}}
{{- range $name, $_ := .Values.unpackerr.env -}}
{{- if regexMatch "^UN_FOLDER_" $name -}}
{{- fail (printf "unpackerr.env.%s is derived from storage.mounts; remove it" $name) -}}
{{- end -}}
{{- if regexMatch "^UN_(SONARR|RADARR|LIDARR)_" $name -}}
{{- fail (printf "unpackerr.env.%s is set by Kubarr through starrLink; remove it" $name) -}}
{{- end -}}
{{- end -}}
{{- if and (default dict .Values.starrLink).enabled (not .Values.starrLink.secretName) -}}
{{- fail "starrLink.secretName is required while starrLink.enabled is true" -}}
{{- end -}}
{{- if and (include "unpackerr.watchFolders" .) (eq (len (include "unpackerr.folders" . | fromJsonArray)) 0) -}}
{{- fail "unpackerr needs at least one writable storage.mounts entry under /downloads/" -}}
{{- end -}}
{{- end }}
