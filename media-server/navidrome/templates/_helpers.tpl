{{- define "navidrome.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "navidrome.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "navidrome.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "navidrome.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
