{{/* GPU fragments for opt-in media-server hardware transcoding. */}}
{{- define "kubarr-common.gpu.resourceName" -}}
{{- $gpu := default (dict) .Values.gpu -}}
{{- if not (has $gpu.provider (list "intel" "nvidia" "amd")) -}}
{{- fail "gpu.provider must be intel, nvidia, or amd" -}}
{{- end -}}
{{- if $gpu.resourceName -}}
{{- $gpu.resourceName -}}
{{- else if eq $gpu.provider "intel" -}}
gpu.intel.com/i915
{{- else if eq $gpu.provider "nvidia" -}}
nvidia.com/gpu
{{- else -}}
{{- fail "gpu.resourceName is required for AMD; configure the installed shared DRM device plugin resource" -}}
{{- end -}}
{{- end -}}

{{- define "kubarr-common.gpu.resources" -}}
{{- $resources := deepCopy .resources -}}
{{- $gpu := default (dict) .root.Values.gpu -}}
{{- range $side := list "requests" "limits" -}}
{{- with (index $resources $side) -}}
{{- range $name, $quantity := . -}}
{{- if eq $quantity nil -}}
{{- $_ := unset (index $resources $side) $name -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if $gpu.enabled -}}
{{- $name := include "kubarr-common.gpu.resourceName" .root -}}
{{- $_ := set $resources "requests" (set (default (dict) ($resources.requests | default (dict))) $name 1) -}}
{{- $_ := set $resources "limits" (set (default (dict) ($resources.limits | default (dict))) $name 1) -}}
{{- end -}}
{{- toYaml $resources -}}
{{- end -}}

{{- define "kubarr-common.gpu.env" -}}
{{- $gpu := default (dict) .Values.gpu -}}
{{- if and $gpu.enabled (eq $gpu.provider "nvidia") -}}
env:
  - name: NVIDIA_DRIVER_CAPABILITIES
    value: "video,utility"
{{- end -}}
{{- end -}}

{{- define "kubarr-common.gpu.podSettings" -}}
{{- $gpu := default (dict) .Values.gpu -}}
{{- if $gpu.enabled -}}
{{- with $gpu.runtimeClassName }}
runtimeClassName: {{ . | quote }}
{{- end }}
{{- end -}}
{{- end -}}

{{- define "kubarr-common.gpu.podSecurityContext" -}}
{{- $context := deepCopy .Values.podSecurityContext -}}
{{- $gpu := default (dict) .Values.gpu -}}
{{- if and $gpu.enabled $gpu.supplementalGroups -}}
{{- $_ := set $context "supplementalGroups" (uniq (concat (default (list) $context.supplementalGroups) $gpu.supplementalGroups)) -}}
{{- end -}}
{{- toYaml $context -}}
{{- end -}}
