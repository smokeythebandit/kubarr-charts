{{/* Jellyfin Enhanced (github.com/n00bcodr/Jellyfin-Enhanced): a pinned,
     checksum-verified release unpacked into Jellyfin's plugin folder before it
     starts. It injects its web UI script while serving index.html, so the
     read-only jellyfin-web folder is never written. Kubarr's worker fills in
     its Seerr, Sonarr and Radarr settings through Jellyfin's plugin API.

     A failed download or a checksum mismatch installs nothing and never blocks
     Jellyfin: the init container logs why and exits 0, and the next start
     tries again. busybox wget does not verify TLS certificates; the pinned
     sha256 is what makes the download trustworthy. */}}
{{- define "jellyfin.jellyfinEnhanced.initContainer" -}}
{{- $je := default dict .Values.jellyfinEnhanced -}}
{{- if $je.enabled }}
{{- if not (regexMatch "^[0-9]+(\\.[0-9]+){3}$" (toString $je.version)) }}{{ fail "jellyfinEnhanced.version must be a four-part plugin version such as 12.15.0.0" }}{{ end }}
{{- if not (regexMatch "^[0-9a-f]{64}$" (toString $je.sha256)) }}{{ fail "jellyfinEnhanced.sha256 must be the release zip's lowercase sha256" }}{{ end }}
- name: install-jellyfin-enhanced
  image: {{ default "busybox:1.37.0" .Values.storage.prepareImage }}
  command:
    - sh
    - -c
    - |
      set -u
      dir="$PLUGIN_DIR/Jellyfin Enhanced_$VERSION"
      mkdir -p "$PLUGIN_DIR" || exit 0
      # Exactly one copy loads: drop other versions, including catalog installs.
      for old in "$PLUGIN_DIR"/"Jellyfin Enhanced_"*; do
        [ -e "$old" ] || continue
        [ "$old" = "$dir" ] && continue
        rm -rf "$old"
      done
      if [ ! -f "$dir/Jellyfin.Plugin.JellyfinEnhanced.dll" ]; then
        work="$PLUGIN_DIR/.jellyfin-enhanced-download"
        rm -rf "$work" && mkdir -p "$work/files"
        if ! wget -q -T 60 -O "$work/plugin.zip" "$URL"; then
          echo "Jellyfin Enhanced $VERSION: download failed; Jellyfin starts without it"
          rm -rf "$work"; exit 0
        fi
        if ! echo "$SHA256  $work/plugin.zip" | sha256sum -c - >/dev/null 2>&1; then
          echo "Jellyfin Enhanced $VERSION: checksum mismatch; not installing"
          rm -rf "$work"; exit 0
        fi
        if ! unzip -q -o "$work/plugin.zip" -d "$work/files"; then
          echo "Jellyfin Enhanced $VERSION: archive unreadable; not installing"
          rm -rf "$work"; exit 0
        fi
        rm -rf "$dir" && mv "$work/files" "$dir"
        rm -rf "$work"
      fi
      # Keep Jellyfin's own status edits (it rewrites meta.json) unless the version changed.
      if [ ! -f "$dir/meta.json" ]; then
        cat > "$dir/meta.json" <<EOF
      {
        "category": "General",
        "description": "Web UI enhancements, Seerr requests and a Sonarr/Radarr calendar and downloads view.",
        "guid": "f69e946a-4b3c-4e9a-8f0a-8d7c1b2c4d9b",
        "name": "Jellyfin Enhanced",
        "overview": "Installed and pinned by Kubarr",
        "owner": "n00bcodr",
        "targetAbi": "$TARGET_ABI",
        "version": "$VERSION",
        "status": "Active",
        "autoUpdate": false,
        "assemblies": ["Jellyfin.Plugin.JellyfinEnhanced.dll"]
      }
      EOF
      fi
      echo "Jellyfin Enhanced $VERSION ready in $dir"
  env:
    - name: PLUGIN_DIR
      value: /config/data/plugins
    - name: VERSION
      value: {{ $je.version | quote }}
    - name: TARGET_ABI
      value: {{ $je.targetAbi | quote }}
    - name: SHA256
      value: {{ $je.sha256 | quote }}
    - name: URL
      value: {{ default (printf "https://github.com/n00bcodr/Jellyfin-Enhanced/releases/download/%s/Jellyfin.Plugin.JellyfinEnhanced_%s.zip" $je.version (regexReplaceAll "\\.0$" (toString $je.targetAbi) "")) $je.url | quote }}
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
