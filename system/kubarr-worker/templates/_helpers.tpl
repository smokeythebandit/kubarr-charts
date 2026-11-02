{{- define "kubarr-worker.fullname" -}}kubarr-worker{{- end }}
{{- define "kubarr-worker.chart" -}}{{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}{{- end }}
{{- define "kubarr-worker.labels" -}}
helm.sh/chart: {{ include "kubarr-worker.chart" . }}
{{ include "kubarr-worker.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
{{- define "kubarr-worker.selectorLabels" -}}
app.kubernetes.io/name: kubarr-worker
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
{{- define "kubarr-worker.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default "kubarr" .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Refuse to render without a password. These charts are public, so no default
password can be shipped here; the connection URI is built from whatever the
operator supplies, or taken from a Secret the Kubarr CLI owns.
*/}}
{{- define "kubarr-worker.database.validate" -}}
{{- if and (not .Values.database.existingSecret) (not .Values.database.password) -}}
{{- fail "kubarr-worker: no database password. This chart ships no default password. Set database.existingSecret to the name of a Secret in this namespace holding keys \"password\" and \"uri\" (the Kubarr CLI creates \"kubarr-db-app\"), or set database.password to the password of the platform database." -}}
{{- end -}}
{{- if and .Values.database.existingSecret (ne .Values.database.existingSecret .Values.database.secretName) -}}
{{- fail "kubarr-worker: database.existingSecret and database.secretName must name the same Secret; the Deployment reads the connection URI from database.secretName." -}}
{{- end -}}
{{- end }}
