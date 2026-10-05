{{- define "frigate.fullname" -}}{{ include "kubarr-common.fullname" . | trunc 49 | trimSuffix "-" }}{{- end }}
{{- define "frigate.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "frigate.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
