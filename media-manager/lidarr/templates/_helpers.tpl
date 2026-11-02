{{- define "lidarr.name" -}}{{ include "kubarr-common.name" . }}{{- end }}
{{- define "lidarr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "lidarr.chart" -}}{{ include "kubarr-common.chart" . }}{{- end }}
{{- define "lidarr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "lidarr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "lidarr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
