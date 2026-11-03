{{- define "openresty.fullname" -}}kubarr-gateway{{- end }}
{{- define "openresty.chart" -}}{{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}{{- end }}
{{- define "openresty.labels" -}}
helm.sh/chart: {{ include "openresty.chart" . }}
{{ include "openresty.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
{{- define "openresty.selectorLabels" -}}
app.kubernetes.io/name: kubarr-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Pod selector labels shared by the Deployment, its spread constraints and its PDB.
*/}}
{{- define "openresty.haSelector" -}}
app.kubernetes.io/name: kubarr-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
