{{- define "jellyfin.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "jellyfin.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "jellyfin.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "jellyfin.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
