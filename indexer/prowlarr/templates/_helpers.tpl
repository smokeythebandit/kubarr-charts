{{- define "prowlarr.name" -}}{{ include "kubarr-common.name" . }}{{- end }}
{{- define "prowlarr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "prowlarr.chart" -}}{{ include "kubarr-common.chart" . }}{{- end }}
{{- define "prowlarr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "prowlarr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "prowlarr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}

{{/*
FlareSolverr URL the setup-flaresolverr service syncs. Upgrades with
--reuse-values keep only the previous release's values, so a release from
before this setting existed falls back to the in-cluster FlareSolverr; an
explicit empty string switches the sync off.
*/}}
{{- define "prowlarr.flaresolverrUrl" -}}
{{- dig "url" "http://flaresolverr.flaresolverr.svc.cluster.local:8191" (default dict .Values.flaresolverr) -}}
{{- end }}
