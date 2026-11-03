{{/*
Expand the name of the chart.
*/}}
{{- define "cloudflared.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "cloudflared.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "cloudflared.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "cloudflared.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
kubarr.io/managed-by: kubarr
{{- end }}

{{/*
Selector labels
*/}}
{{- define "cloudflared.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cloudflared.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name
*/}}
{{- define "cloudflared.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "cloudflared.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Pod selector labels shared by the Deployment, its spread constraints and its PDB.
*/}}
{{- define "cloudflared.haSelector" -}}
{{ include "cloudflared.selectorLabels" . }}
{{- end -}}
