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
{{- define "redmine.dbSubPath" -}}{{ default (printf "development/redmine/%s/postgres" (include "redmine.fullname" .)) .Values.database.bundled.persistence.subPath }}{{- end }}
{{- define "redmine.filesSubPath" -}}{{ default (printf "development/redmine/%s/files" (include "redmine.fullname" .)) .Values.storage.attachments.subPath }}{{- end }}
