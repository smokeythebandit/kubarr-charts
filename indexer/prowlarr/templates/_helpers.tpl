{{- define "prowlarr.name" -}}{{ include "kubarr-common.name" . }}{{- end }}
{{- define "prowlarr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "prowlarr.chart" -}}{{ include "kubarr-common.chart" . }}{{- end }}
{{- define "prowlarr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "prowlarr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "prowlarr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
