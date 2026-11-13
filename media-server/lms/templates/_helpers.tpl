{{- define "lms.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "lms.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "lms.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "lms.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
