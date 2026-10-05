{{- define "immich.fullname" -}}{{ include "kubarr-common.fullname" . | trunc 54 | trimSuffix "-" }}{{- end }}
{{- define "immich.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "immich.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "immich.dbSecret" -}}{{ default (printf "%s-db" (include "immich.fullname" .)) .Values.database.existingSecret }}{{- end }}
{{- define "immich.mlSubPath" -}}{{ default (printf "media-server/immich/%s/models" (include "immich.fullname" .)) .Values.machineLearning.cache.subPath }}{{- end }}
{{- define "immich.dbHost" -}}
{{- if .Values.database.bundled.enabled -}}{{ include "immich.fullname" . }}-postgres{{- else -}}{{ required "database.host is required when bundled PostgreSQL is disabled" .Values.database.host }}{{- end -}}
{{- end }}
{{- define "immich.cacheHost" -}}
{{- if .Values.cache.bundled.enabled -}}{{ include "immich.fullname" . }}-valkey{{- else -}}{{ required "cache.host is required when bundled Valkey is disabled" .Values.cache.host }}{{- end -}}
{{- end }}
{{- define "immich.dbEnv" -}}
- name: DB_HOSTNAME
  value: {{ include "immich.dbHost" . | quote }}
- name: DB_PORT
  value: {{ .Values.database.port | quote }}
- name: DB_USERNAME
  value: {{ .Values.database.username | quote }}
- name: DB_DATABASE_NAME
  value: {{ .Values.database.name | quote }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "immich.dbSecret" . | quote }}
      key: {{ .Values.database.passwordKey | quote }}
- name: REDIS_HOSTNAME
  value: {{ include "immich.cacheHost" . | quote }}
- name: REDIS_PORT
  value: {{ .Values.cache.port | quote }}
{{- with .Values.cache.existingSecret }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ . | quote }}
      key: REDIS_PASSWORD
{{- end }}
{{- end }}
{{- define "immich.security" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}
