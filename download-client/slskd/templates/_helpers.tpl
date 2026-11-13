{{- define "slskd.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "slskd.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "slskd.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "slskd.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
