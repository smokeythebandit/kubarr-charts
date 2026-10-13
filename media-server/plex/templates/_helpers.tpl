{{- define "plex.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "plex.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "plex.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "plex.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}
