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
The label that admits a pod to the database as a dump/restore client. The
nightly backup job carries it, and so does the pod `kubarr db restore` runs:
the NetworkPolicy selects on this label alone so both are admitted.
*/}}
{{- define "postgresql.backupSelectorLabels" -}}
app.kubernetes.io/component: database-backup
{{- end }}

{{/*
Name of the CLI-owned Secret holding the database credentials (basic-auth:
username, password; plus uri). Refuses to render without one: this chart ships
no default password, because a password committed to a public repository is
the password of every install that uses it.
*/}}
{{- define "postgresql.database.secretName" -}}
{{- $database := default dict .Values.database -}}
{{- $name := default "" $database.existingSecret -}}
{{- if not $name -}}
{{- fail "postgresql: no database credential. This chart ships no default password. Set database.existingSecret to the name of a kubernetes.io/basic-auth Secret in this namespace holding keys \"username\", \"password\" and \"uri\" (the Kubarr CLI creates \"kubarr-db-app\")." -}}
{{- end -}}
{{- $name -}}
{{- end }}

{{/* Writable backup destination on the shared export, from the declared requirement. */}}
{{- define "postgresql.backup.target" -}}
{{- first (dig "storage" "required" "main" (list "/backups") (default dict .Values.kubarr)) -}}
{{- end }}
