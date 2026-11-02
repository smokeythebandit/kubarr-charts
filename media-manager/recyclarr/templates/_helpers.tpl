{{- define "recyclarr.name" -}}{{ include "kubarr-common.name" . }}{{- end }}
{{- define "recyclarr.fullname" -}}{{ include "kubarr-common.fullname" . }}{{- end }}
{{- define "recyclarr.chart" -}}{{ include "kubarr-common.chart" . }}{{- end }}
{{- define "recyclarr.labels" -}}{{ include "kubarr-common.labels" . }}{{- end }}
{{- define "recyclarr.selectorLabels" -}}{{ include "kubarr-common.selectorLabels" . }}{{- end }}
{{- define "recyclarr.serviceAccountName" -}}{{ include "kubarr-common.serviceAccountName" . }}{{- end }}

{{/* The environment variable each instance's API key arrives in. Derived, so a
     values change cannot leave the Secret reference and the config disagreeing. */}}
{{- define "recyclarr.apiKeyEnv" -}}
{{- printf "%s_API_KEY" (.service | upper) -}}
{{- end }}

{{/* True only when an instance is both enabled and pointed at a Secret. An
     instance without a key cannot sync, so it is rendered commented out rather
     than failing every run. */}}
{{- define "recyclarr.instanceActive" -}}
{{- $i := .instance -}}
{{- if and $i.enabled (ne (default "" (default dict $i.apiKeySecret).name) "") -}}true{{- end -}}
{{- end }}

{{/* Reject anything recyclarr cannot sync before a Job is created for it. */}}
{{- define "recyclarr.validate" -}}
{{- $cron := .Values.recyclarr.cronJob -}}
{{- if not (has $cron.concurrencyPolicy (list "Allow" "Forbid" "Replace")) -}}
{{- fail "recyclarr.cronJob.concurrencyPolicy must be Allow, Forbid or Replace" -}}
{{- end -}}
{{- if not (has $cron.restartPolicy (list "OnFailure" "Never")) -}}
{{- fail "recyclarr.cronJob.restartPolicy must be OnFailure or Never; a Job cannot use Always" -}}
{{- end -}}
{{- if not (kindIs "string" $cron.schedule) -}}
{{- fail "recyclarr.cronJob.schedule must be a cron expression string" -}}
{{- end -}}
{{- if not (has .Values.recyclarr.sync.service (list "" "radarr" "sonarr")) -}}
{{- fail "recyclarr.sync.service must be empty (both), radarr or sonarr" -}}
{{- end -}}
{{- range $service, $instance := .Values.recyclarr.instances -}}
{{- if not (has $service (list "radarr" "sonarr")) -}}
{{- fail (printf "recyclarr.instances.%s is not supported; recyclarr syncs radarr and sonarr only" $service) -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9_-]*$" (toString $instance.name)) -}}
{{- fail (printf "recyclarr.instances.%s.name must be a simple instance name" $service) -}}
{{- end -}}
{{- if and $instance.enabled (not (hasPrefix "http://" (toString $instance.baseUrl))) (not (hasPrefix "https://" (toString $instance.baseUrl))) -}}
{{- fail (printf "recyclarr.instances.%s.baseUrl must be an http(s) URL" $service) -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/* One service's block of recyclarr.yml. Commented out verbatim while the
     instance has no API key Secret, so the shape a user has to fill in is the
     shape the chart would have rendered. */}}
{{- define "recyclarr.instanceConfig" -}}
{{- $service := .service -}}
{{- $i := .instance -}}
{{- $p := ternary "" "# " (eq (include "recyclarr.instanceActive" .) "true") -}}
#
# {{ $service | title }} -- instance {{ $i.name }}
#
{{- if $p }}
{{- if not $i.enabled }}
# Disabled: set recyclarr.instances.{{ $service }}.enabled=true, and point
# apiKeySecret.name at a Secret holding {{ $service }}'s API key, to render this
# block live.
{{- else }}
# Not synced yet: set recyclarr.instances.{{ $service }}.apiKeySecret.name to a
# Secret holding {{ $service }}'s API key and this block is rendered live.
{{- end }}
{{- end }}
{{ $p }}{{ $service }}:
{{ $p }}  {{ $i.name }}:
{{ $p }}    base_url: {{ $i.baseUrl }}
{{ $p }}    # Read from the environment, which the CronJob fills from a Secret.
{{ $p }}    api_key: !env_var {{ include "recyclarr.apiKeyEnv" (dict "service" $service) }}
{{ $p }}    quality_definition:
{{ $p }}      type: {{ $i.qualityDefinition }}
{{ $p }}    quality_profiles:
{{- range $profile := $i.qualityProfiles }}
{{ $p }}      - trash_id: {{ $profile.trash_id }}{{ with $profile.name }}  # {{ . }}{{ end }}
{{ $p }}        # Matches the TRaSH templates: scores the guide does not mention
{{ $p }}        # are reset instead of being left behind from a previous sync.
{{ $p }}        reset_unmatched_scores:
{{ $p }}          enabled: true
{{- end }}
{{ $p }}    # Custom format groups are not synced by default: each one rewrites
{{ $p }}    # scoring across every profile it touches. Pick the groups you want
{{ $p }}    # from https://recyclarr.dev/guide/cf-groups/ and list them here.
{{ $p }}    # custom_format_groups:
{{ $p }}    #   add:
{{ $p }}    #     - trash_id: <group id from the page above>
{{- end }}

{{/*
Whether /config is backed by a claim. False means an emptyDir: recyclarr
rebuilds the TRaSH clone and its sync state from upstream, and a claim cannot
bind at install time because this chart's only workload is a CronJob that
creates no pod while suspended.
*/}}
{{- define "recyclarr.configPersistent" -}}
{{- $config := .Values.storage.config -}}
{{- if or $config.create (ne (default "" $config.existingClaim) "") -}}true{{- end -}}
{{- end -}}
