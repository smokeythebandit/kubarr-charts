{{- define "bazarr.name" -}}{{ include "kubarr-common.name" . }}{{- end }}
{{- define "bazarr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "bazarr.chart" -}}{{ include "kubarr-common.chart" . }}{{- end }}
{{- define "bazarr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "bazarr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "bazarr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
