{{- define "kubarr-dns-webhook.name" -}}
kubarr-dns-webhook
{{- end -}}

{{- define "kubarr-dns-webhook.labels" -}}
app.kubernetes.io/name: {{ include "kubarr-dns-webhook.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
Pod selector labels shared by the Deployment, its spread constraints and its PDB.
*/}}
{{- define "kubarr-dns-webhook.haSelector" -}}
app.kubernetes.io/name: {{ include "kubarr-dns-webhook.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
