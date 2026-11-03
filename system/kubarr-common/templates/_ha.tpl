{{/*
Multi-node high availability helpers.

Charts opt in through the value `kubarr.ha.nodeCount` (int, default 1). At 1 or
below (or absent) every helper renders nothing special, so a single-node install
keeps exactly today's shape: one replica, no PDB, no spread constraints.
*/}}

{{/* Node count from kubarr.ha.nodeCount; 1 when unset or invalid. */}}
{{- define "kubarr-common.ha.nodeCount" -}}
{{- $kubarr := default dict .Values.kubarr -}}
{{- $ha := default dict (get $kubarr "ha") -}}
{{- $n := int (default 1 (get $ha "nodeCount")) -}}
{{- if lt $n 1 }}1{{ else }}{{ $n }}{{ end -}}
{{- end -}}

{{/*
Replica count: 1 on a single node, otherwise min(nodeCount, max).
Usage: include "kubarr-common.ha.replicas" (dict "root" . "max" 3)
*/}}
{{- define "kubarr-common.ha.replicas" -}}
{{- $n := int (include "kubarr-common.ha.nodeCount" .root) -}}
{{- if le $n 1 }}1{{ else }}{{ min $n (int .max) }}{{ end -}}
{{- end -}}

{{/*
Pod spread across nodes; empty unless replicas > 1.
nodeTaintsPolicy Honor keeps tainted nodes (control planes, unreachable nodes)
out of the skew count: as empty domains they would pin the minimum at 0 and
hold a rolling update's surge pod Pending forever.
Usage: include "kubarr-common.ha.spread" (dict "root" . "replicas" $r "selector" $matchLabels)
*/}}
{{- define "kubarr-common.ha.spread" -}}
{{- if gt (int .replicas) 1 -}}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    nodeTaintsPolicy: Honor
    labelSelector:
      matchLabels:
        {{- toYaml .selector | nindent 8 }}
{{- end -}}
{{- end -}}

{{/*
PodDisruptionBudget allowing one pod down; empty unless replicas > 1.
Usage: include "kubarr-common.ha.pdb" (dict "root" . "replicas" $r "name" "x" "namespace" "ns" "selector" $matchLabels)
*/}}
{{- define "kubarr-common.ha.pdb" -}}
{{- if gt (int .replicas) 1 -}}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ .name }}
  namespace: {{ .namespace }}
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      {{- toYaml .selector | nindent 6 }}
{{- end -}}
{{- end -}}
