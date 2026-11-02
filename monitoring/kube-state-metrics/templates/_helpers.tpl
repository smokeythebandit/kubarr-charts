{{/* Names and labels come from kubarr-common; only the collector catalogue is
     local to this chart. */}}

{{/* Every object kind this chart is willing to collect, mapped to the API
     group that owns it. The map is the single source of truth for both the
     --resources flag and the ClusterRole, so the grant can never be wider
     than what is actually scraped.

     configmaps and secrets are absent on purpose: their collectors emit only
     metadata, but get/list/watch on them is read access to every credential
     in the cluster. */}}
{{- define "kube-state-metrics.catalogue" -}}
{{- $catalogue := dict
  "namespaces" ""
  "nodes" ""
  "pods" ""
  "services" ""
  "serviceaccounts" ""
  "endpoints" ""
  "persistentvolumes" ""
  "persistentvolumeclaims" ""
  "replicationcontrollers" ""
  "resourcequotas" ""
  "limitranges" ""
  "deployments" "apps"
  "daemonsets" "apps"
  "statefulsets" "apps"
  "replicasets" "apps"
  "jobs" "batch"
  "cronjobs" "batch"
  "horizontalpodautoscalers" "autoscaling"
  "poddisruptionbudgets" "policy"
  "ingresses" "networking.k8s.io"
  "ingressclasses" "networking.k8s.io"
  "networkpolicies" "networking.k8s.io"
  "storageclasses" "storage.k8s.io"
  "volumeattachments" "storage.k8s.io"
  "leases" "coordination.k8s.io"
  "certificatesigningrequests" "certificates.k8s.io"
  "mutatingwebhookconfigurations" "admissionregistration.k8s.io"
  "validatingwebhookconfigurations" "admissionregistration.k8s.io"
-}}
{{- toYaml $catalogue -}}
{{- end }}

{{/* The validated collector list, in the order declared. */}}
{{- define "kube-state-metrics.collectors" -}}
{{- $catalogue := fromYaml (include "kube-state-metrics.catalogue" .) -}}
{{- $collectors := .Values.kubeStateMetrics.collectors -}}
{{- if not (kindIs "slice" $collectors) -}}
{{- fail "kubeStateMetrics.collectors must be a list" -}}
{{- end -}}
{{- if not $collectors -}}
{{- fail "kubeStateMetrics.collectors must name at least one object kind" -}}
{{- end -}}
{{- $seen := list -}}
{{- range $collector := $collectors -}}
{{- if or (not (kindIs "string" $collector)) (eq $collector "") -}}
{{- fail "kubeStateMetrics.collectors entries must be nonempty strings" -}}
{{- end -}}
{{- if has $collector (list "configmaps" "secrets") -}}
{{- fail (printf "kubeStateMetrics.collectors: %s is refused; collecting it requires read access to every %s in the cluster" $collector $collector) -}}
{{- end -}}
{{- if not (hasKey $catalogue $collector) -}}
{{- fail (printf "kubeStateMetrics.collectors: %s is not a supported object kind; add it to the chart's catalogue with its API group first, so the ClusterRole stays in step" $collector) -}}
{{- end -}}
{{- if has $collector $seen -}}
{{- fail (printf "kubeStateMetrics.collectors: %s is listed twice" $collector) -}}
{{- end -}}
{{- $seen = append $seen $collector -}}
{{- end -}}
{{- join "," $collectors -}}
{{- end }}

{{/* Read-only policy rules covering exactly the configured collectors. */}}
{{- define "kube-state-metrics.rules" -}}
{{- $catalogue := fromYaml (include "kube-state-metrics.catalogue" .) -}}
{{- $_ := include "kube-state-metrics.collectors" . -}}
{{- $byGroup := dict -}}
{{- range $collector := .Values.kubeStateMetrics.collectors -}}
{{- $group := index $catalogue $collector -}}
{{- $resources := append (default list (index $byGroup $group)) $collector -}}
{{- $_ := set $byGroup $group $resources -}}
{{- end -}}
{{- range $group := keys $byGroup | sortAlpha }}
- apiGroups: [{{ $group | quote }}]
  resources:
    {{- range $resource := index $byGroup $group | sortAlpha }}
    - {{ $resource }}
    {{- end }}
  verbs: ["get", "list", "watch"]
{{- end }}
{{- end }}
