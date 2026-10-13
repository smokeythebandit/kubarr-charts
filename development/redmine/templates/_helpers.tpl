{{- define "redmine.fullname" -}}{{ include "kubarr-common.fullname" . | trunc 54 | trimSuffix "-" }}{{- end }}
{{- define "redmine.dbHost" -}}
{{- if .Values.database.bundled.enabled -}}{{ include "redmine.fullname" . }}-postgres{{- else -}}{{ required "database.host is required for external PostgreSQL" .Values.database.host }}{{- end -}}
{{- end }}
{{- define "redmine.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "redmine.selector" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "redmine.postgresSelector" -}}
app.kubernetes.io/name: redmine-postgres
app.kubernetes.io/instance: {{ .Release.Name }}
app: redmine-postgres
{{- end }}
{{- define "redmine.credentialsSecret" -}}{{ default (printf "%s-credentials" (include "redmine.fullname" .)) .Values.secrets.existingSecret }}{{- end }}
{{- define "redmine.dbSubPath" -}}{{- include "kubarr-common.storage.requiredSource" (dict "root" . "mounts" .Values.storage.workloads.postgres.mounts "target" "/var/lib/postgresql/data" "label" "storage.workloads.postgres.mounts" "reservedPaths" (list "/dev/shm")) -}}{{- end }}
{{- define "redmine.filesSubPath" -}}{{- if and (not .Values.storage.attachments.existingClaim) (not .Values.storage.attachments.storageClassName) -}}{{- include "kubarr-common.storage.requiredSource" (dict "root" . "mounts" .Values.storage.mounts "target" "/usr/src/redmine/files" "reservedPaths" (list "/usr/src/redmine/config.ru")) -}}{{- end -}}{{- end }}
