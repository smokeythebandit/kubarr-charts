{{- define "komga.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "komga.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "komga.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "komga.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
{{/* Komga's actuator lives under the servlet context path, so the probe path
     follows basePath. An empty basePath leaves it at the server root. */}}
{{- define "komga.healthPath" -}}{{ printf "%s/actuator/health" .Values.komga.basePath }}{{- end }}
