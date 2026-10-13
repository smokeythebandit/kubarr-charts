{{- define "jellyseerr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "jellyseerr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "jellyseerr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "jellyseerr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
