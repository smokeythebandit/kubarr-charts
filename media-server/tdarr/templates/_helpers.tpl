{{/* Room for the "-node" suffix inside the 63-character limit. */}}
{{- define "tdarr.fullname" -}}{{ include "kubarr-common.fullname" . | trunc 58 | trimSuffix "-" }}{{- end }}
{{- define "tdarr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "tdarr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "tdarr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}

{{/* Nodes connect outbound to the server; serverURL is the documented way to
     point one at it, and it is regenerated from this value on every start. */}}
{{- define "tdarr.serverURL" -}}
{{- printf "http://%s.%s.svc.cluster.local:%v" (include "tdarr.fullname" .) .Values.namespace.name .Values.tdarr.service.serverPort -}}
{{- end }}

{{/* Shared by the server (seededApiKey plus its internal Node) and the Node
     pods (apiKey). Empty secret means no key is injected anywhere. */}}
{{- define "tdarr.apiKeyEnv" -}}
{{- with .Values.auth.existingSecret }}
- name: apiKey
  valueFrom:
    secretKeyRef:
      name: {{ . | quote }}
      key: {{ $.Values.auth.key | quote }}
{{- end }}
{{- end }}

{{- define "tdarr.validate" -}}
{{- $app := .Values.tdarr -}}
{{- $node := .Values.node -}}
{{- if ne (int $app.service.targetPort) (int $app.service.port) -}}
{{- fail "tdarr.service.port and tdarr.service.targetPort both address the web UI; keep them equal" -}}
{{- end -}}
{{- if eq (int $app.service.serverPort) (int $app.service.targetPort) -}}
{{- fail "tdarr.service.serverPort must differ from the web UI port" -}}
{{- end -}}
{{- if ne $app.service.type "ClusterIP" -}}
{{- fail "tdarr.service.type must be ClusterIP; publish the web UI through Kubarr's gateway" -}}
{{- end -}}
{{- if ne $node.image.tag $app.image.tag -}}
{{- fail "node.image.tag must equal tdarr.image.tag; a Node refuses to connect to a Server of a different release" -}}
{{- end -}}
{{- if not (has (toString $app.ffmpegVersion) (list "6" "7")) -}}
{{- fail "tdarr.ffmpegVersion must be 6 or 7" -}}
{{- end -}}
{{- if not (has $node.nodeType (list "mapped" "unmapped")) -}}
{{- fail "node.nodeType must be mapped or unmapped" -}}
{{- end -}}
{{- if $node.enabled -}}
  {{- if lt (int $node.replicaCount) 0 -}}{{- fail "node.replicaCount must not be negative" -}}{{- end -}}
  {{- if and $node.name (gt (int $node.replicaCount) 1) -}}
  {{- fail "node.name applies to a single Node; leave it empty so each replica registers under its own pod name" -}}
  {{- end -}}
  {{- if and .Values.auth.enabled (not .Values.auth.existingSecret) -}}
  {{- fail "auth.existingSecret is required for Node pods: a Node cannot connect without an API key. Create a Secret holding a tapi_ key, or set auth.enabled=false if nothing untrusted can reach the server" -}}
  {{- end -}}
{{- end -}}
{{- if and .Values.auth.existingSecret (not .Values.auth.key) -}}
{{- fail "auth.key is required with auth.existingSecret" -}}
{{- end -}}
{{- end }}
