{{- define "managed-nfs.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "managed-nfs.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "managed-nfs.labels" -}}
app.kubernetes.io/name: {{ include "managed-nfs.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "managed-nfs.selectorLabels" -}}
app.kubernetes.io/name: {{ include "managed-nfs.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: kubarr-managed-nfs
{{- end }}
