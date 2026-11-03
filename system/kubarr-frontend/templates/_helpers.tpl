{{- define "kubarr-frontend.fullname" -}}kubarr-frontend{{- end }}
{{- define "kubarr-frontend.chart" -}}{{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}{{- end }}
{{- define "kubarr-frontend.labels" -}}
helm.sh/chart: {{ include "kubarr-frontend.chart" . }}
{{ include "kubarr-frontend.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
{{- define "kubarr-frontend.selectorLabels" -}}
app.kubernetes.io/name: kubarr-frontend
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Pod selector labels shared by the Deployment, its spread constraints and its PDB.
*/}}
{{- define "kubarr-frontend.haSelector" -}}
app.kubernetes.io/name: kubarr-frontend
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
