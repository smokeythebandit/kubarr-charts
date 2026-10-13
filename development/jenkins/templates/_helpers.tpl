{{- define "jenkins.fullname" -}}{{ include "kubarr-common.fullname" . | trunc 58 | trimSuffix "-" }}{{- end }}
{{- define "jenkins.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "jenkins.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
