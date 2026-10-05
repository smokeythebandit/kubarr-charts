{{- define "scm-manager.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "scm-manager.claimName" -}}{{ include "scm-manager.fullname" . | trunc 58 | trimSuffix "-" }}-home{{- end }}
{{- define "scm-manager.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "scm-manager.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "scm-manager.homeSubPath" -}}{{ default (printf "development/scm-manager/%s/home" (include "scm-manager.fullname" .)) .Values.storage.home.subPath }}{{- end }}
