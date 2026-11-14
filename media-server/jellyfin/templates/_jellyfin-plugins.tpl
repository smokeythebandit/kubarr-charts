{{/* Companion plugins Jellyfin Enhanced lists under Overview > Optional
     Dependencies (File Transformation, Plugin Pages, Custom Tabs, Intro
     Skipper, In-Player Episode Preview). Each is a pinned, checksum-verified
     release unpacked into Jellyfin's plugin folder before it starts, the same
     way as Jellyfin Enhanced itself.

     A failed download or a checksum mismatch skips that plugin only and never
     blocks Jellyfin: the init container logs why and the next start tries
     again. Exactly one copy of each loads, so other versions of a pinned
     plugin, including catalog installs, are removed; to update one, change its
     version, url and sha256 together. busybox wget does not verify TLS
     certificates; the pinned sha256 is what makes each download trustworthy. */}}
{{- define "jellyfin.plugins.selected" -}}
{{- $selected := list -}}
{{- range (default dict .Values.jellyfinPlugins).plugins -}}
{{- if .enabled -}}{{- $selected = append $selected . -}}{{- end -}}
{{- end -}}
{{- if and (default dict .Values.jellyfinPlugins).enabled $selected -}}{{- toJson $selected -}}{{- end -}}
{{- end -}}

{{- define "jellyfin.plugins.initContainer" -}}
{{- $raw := include "jellyfin.plugins.selected" . -}}
{{- if $raw }}
{{- $plugins := fromJsonArray $raw -}}
- name: install-jellyfin-plugins
  image: {{ default "busybox:1.37.0" .Values.storage.prepareImage }}
  command:
    - sh
    - -c
    - |
      set -u
      mkdir -p "$PLUGIN_DIR" || exit 0
      # install NAME VERSION GUID TARGET_ABI ASSEMBLY URL SHA256 DESCRIPTION
      install() {
        name="$1"; version="$2"; guid="$3"; abi="$4"; dll="$5"; url="$6"; sha="$7"; desc="$8"
        dir="$PLUGIN_DIR/${name}_$version"
        for old in "$PLUGIN_DIR/${name}_"*; do
          [ -e "$old" ] || continue
          [ "$old" = "$dir" ] && continue
          rm -rf "$old"
        done
        if [ ! -f "$dir/$dll" ]; then
          work="$PLUGIN_DIR/.kubarr-plugin-download"
          rm -rf "$work" && mkdir -p "$work/files"
          if ! wget -q -T 60 -O "$work/plugin.zip" "$url"; then
            echo "$name $version: download failed; Jellyfin starts without it"
            rm -rf "$work"; return 0
          fi
          if ! echo "$sha  $work/plugin.zip" | sha256sum -c - >/dev/null 2>&1; then
            echo "$name $version: checksum mismatch; not installing"
            rm -rf "$work"; return 0
          fi
          if ! unzip -q -o "$work/plugin.zip" -d "$work/files" || [ ! -f "$work/files/$dll" ]; then
            echo "$name $version: archive unreadable or missing $dll; not installing"
            rm -rf "$work"; return 0
          fi
          rm -rf "$dir" && mv "$work/files" "$dir"
          rm -rf "$work"
        fi
        # Keep Jellyfin's own status edits (it rewrites meta.json) unless the version changed.
        if [ ! -f "$dir/meta.json" ]; then
          cat > "$dir/meta.json" <<EOF
      {
        "category": "General",
        "description": "$desc",
        "guid": "$guid",
        "name": "$name",
        "overview": "Installed and pinned by Kubarr",
        "targetAbi": "$abi",
        "version": "$version",
        "status": "Active",
        "autoUpdate": false,
        "assemblies": ["$dll"]
      }
      EOF
        fi
        echo "$name $version ready in $dir"
      }
      {{- range $plugins }}
      {{- if not (regexMatch "^[0-9]+(\\.[0-9]+){3}$" (toString .version)) }}{{ fail (printf "jellyfinPlugins: %s version must be a four-part plugin version" .name) }}{{ end }}
      {{- if not (regexMatch "^[0-9a-f]{64}$" (toString .sha256)) }}{{ fail (printf "jellyfinPlugins: %s sha256 must be the release zip's lowercase sha256" .name) }}{{ end }}
      {{- if not (regexMatch "^[0-9a-fA-F-]{36}$" (toString .guid)) }}{{ fail (printf "jellyfinPlugins: %s guid must be the plugin's GUID" .name) }}{{ end }}
      {{- if not (regexMatch "^[A-Za-z0-9 ._-]+$" (toString .name)) }}{{ fail (printf "jellyfinPlugins: plugin name %q has characters a folder name cannot carry" .name) }}{{ end }}
      {{- if not (regexMatch "^https://" (toString .url)) }}{{ fail (printf "jellyfinPlugins: %s url must be https" .name) }}{{ end }}
      install {{ .name | squote }} {{ .version | squote }} {{ .guid | squote }} {{ .targetAbi | squote }} {{ .assembly | squote }} {{ .url | squote }} {{ .sha256 | squote }} {{ .description | replace "'" "" | replace "\"" "" | squote }}
      {{- end }}
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
      cpu: 500m
      memory: 128Mi
  volumeMounts:
    {{- include "kubarr-common.storage.configMount" (dict "root" .) | nindent 4 }}
{{- end }}
{{- end -}}
