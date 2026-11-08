{{/* Whether sign-in with Kubarr is wired: plugin installed and key given. */}}
{{- define "jellyfin.kubarrSso" -}}
{{- if and (default dict .Values.kubarrPlugin).enabled (default dict .Values.kubarrIdentity).secretName -}}true{{- end -}}
{{- end -}}

{{- define "jellyfin.kubarrPlugin.initContainer" -}}
{{- if (default dict .Values.kubarrPlugin).enabled }}
- name: install-kubarr-plugin
  image: "{{ .Values.kubarrPlugin.image.repository }}:{{ .Values.kubarrPlugin.image.tag }}"
  imagePullPolicy: {{ .Values.kubarrPlugin.image.pullPolicy }}
  env:
    - name: PLUGIN_DIR
      value: /config/data/plugins
  securityContext:
    runAsUser: {{ .Values.jellyfin.env.PUID | int }}
    runAsGroup: {{ .Values.jellyfin.env.PGID | int }}
    runAsNonRoot: {{ ne (int .Values.jellyfin.env.PUID) 0 }}
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop:
        - ALL
  resources:
    requests:
      cpu: 10m
      memory: 16Mi
    limits:
      cpu: 200m
      memory: 64Mi
  volumeMounts:
    {{- include "kubarr-common.storage.configMount" (dict "root" .) | nindent 4 }}
{{- end }}
{{- end -}}

{{- define "jellyfin.kubarrPlugin.env" -}}
{{- if (default dict .Values.kubarrPlugin).enabled }}
- name: KUBARR_API_URL
  value: {{ .Values.kubarrPlugin.apiUrl | quote }}
- name: KUBARR_GPU_VENDOR
  value: {{ ternary (default dict .Values.gpu).provider "" (default false (default dict .Values.gpu).enabled) | quote }}
{{- end }}
{{- end -}}

{{- define "jellyfin.kubarrPlugin.mounts" -}}
{{- if (default dict .Values.kubarrPlugin).enabled }}
- name: kubarr-bootstrap
  mountPath: /kubarr-config
  readOnly: true
{{- if (default dict .Values.jellyfinIntegration).secretName }}
- name: api-key-secret
  mountPath: /kubarr-secrets/jellyfin
  readOnly: true
{{- end }}
{{- if (default dict .Values.kubarrIdentity).secretName }}
- name: kubarr-identity
  mountPath: /kubarr-secrets/jellyfin-identity
  readOnly: true
{{- end }}
{{- end }}
{{- end -}}

{{- define "jellyfin.kubarrPlugin.volumes" -}}
{{- if (default dict .Values.kubarrPlugin).enabled }}
- name: kubarr-bootstrap
  configMap:
    name: {{ include "jellyfin.fullname" . }}-kubarr-bootstrap
{{- if (default dict .Values.kubarrIdentity).secretName }}
- name: kubarr-identity
  secret:
    secretName: {{ .Values.kubarrIdentity.secretName }}
    items:
      - key: api-key
        path: api-key
{{- end }}
{{- end }}
{{- end -}}
