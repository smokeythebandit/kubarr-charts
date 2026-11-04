{{/*
Name of the cluster-scoped RBAC objects. Namespaced objects keep upstream's
names; these cannot, because a cluster that already runs upstream's manifest
(kind, k3s, a hand install) owns `local-path-provisioner-role`/`-bind`.
*/}}
{{- define "local-path-provisioner.clusterName" -}}
{{- printf "kubarr-local-path-provisioner-%s" .Values.namespace.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
