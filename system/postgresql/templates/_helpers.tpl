{{/*
Expand the name of the chart.
*/}}
{{- define "postgresql.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "postgresql.labels" -}}
app.kubernetes.io/name: {{ include "postgresql.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "postgresql.selectorLabels" -}}
app.kubernetes.io/name: {{ include "postgresql.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Name of the Secret holding the database credentials (keys: password, uri).

`database.existingSecret` names a Secret the Kubarr CLI owns and created in
this namespace; empty means this chart templates its own from
`database.password`. Either way the StatefulSet reads the password from a
Secret, never from a literal in the Pod template.
*/}}
{{- define "postgresql.database.secretName" -}}
{{- default "kubarr-db-app" .Values.database.existingSecret -}}
{{- end }}

{{/*
Refuse to render without a password. This chart ships no default: a password
committed to a public repository is the password of every install that uses it.
*/}}
{{- define "postgresql.database.validate" -}}
{{- if and (not .Values.database.existingSecret) (not .Values.database.password) -}}
{{- fail "postgresql: no database password. This chart ships no default password. Set database.existingSecret to the name of a Secret in this namespace holding keys \"password\" and \"uri\" (the Kubarr CLI creates \"kubarr-db-app\"), or set database.password to a password you generate yourself." -}}
{{- end -}}
{{- end }}
