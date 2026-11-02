{{- define "calibre-web-automated.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "calibre-web-automated.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "calibre-web-automated.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "calibre-web-automated.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
