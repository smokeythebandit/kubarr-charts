{{- /*
Tubifarry is installed only for the slskd link, the one integration that
needs it. Lidarr keeps the plugin updated afterwards, so an existing copy of
any version is left alone; this only fills an empty plugin folder.
*/ -}}
{{- define "lidarr.tubifarry.enabled" -}}
{{- if and (default dict .Values.tubifarry).enabled (default dict .Values.slskdIntegration).secretName -}}true{{- end -}}
{{- end -}}

{{- define "lidarr.tubifarry.initContainer" -}}
{{- if include "lidarr.tubifarry.enabled" . }}
{{- $t := .Values.tubifarry -}}
{{- if not (regexMatch "^[0-9]+(\\.[0-9]+){2,3}$" (toString $t.version)) }}{{ fail "tubifarry.version must be a release version such as 2.3.0.1" }}{{ end }}
{{- if not (regexMatch "^[0-9a-f]{64}$" (toString $t.sha256)) }}{{ fail "tubifarry.sha256 must be the release zip's lowercase sha256" }}{{ end }}
- name: install-tubifarry
  image: busybox:1.38.0
  securityContext:
    runAsUser: {{ .Values.lidarr.env.PUID | int }}
    runAsGroup: {{ .Values.lidarr.env.PGID | int }}
    runAsNonRoot: {{ ne (int .Values.lidarr.env.PUID) 0 }}
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
  command:
    - sh
    - -c
    - |
      set -u
      dir=/config/plugins/TypNull/Tubifarry
      if [ -f "$dir/Lidarr.Plugin.Tubifarry.dll" ]; then
        echo "Tubifarry already installed; Lidarr manages its updates"
        exit 0
      fi
      work=/config/plugins/.tubifarry-download
      rm -rf "$work" && mkdir -p "$work/files" || exit 0
      if ! wget -q -T 60 -O "$work/plugin.zip" "$URL"; then
        echo "Tubifarry $VERSION: download failed; Lidarr starts without it"
        rm -rf "$work"; exit 0
      fi
      if ! echo "$SHA256  $work/plugin.zip" | sha256sum -c - >/dev/null 2>&1; then
        echo "Tubifarry $VERSION: checksum mismatch; not installing"
        rm -rf "$work"; exit 0
      fi
      if ! unzip -q -o "$work/plugin.zip" -d "$work/files"; then
        echo "Tubifarry $VERSION: archive unreadable; not installing"
        rm -rf "$work"; exit 0
      fi
      mkdir -p "$(dirname "$dir")" && rm -rf "$dir" && mv "$work/files" "$dir"
      rm -rf "$work"
      echo "Tubifarry $VERSION installed in $dir"
  env:
    - name: VERSION
      value: {{ $t.version | quote }}
    - name: SHA256
      value: {{ $t.sha256 | quote }}
    - name: URL
      value: {{ default (printf "https://github.com/TypNull/Tubifarry/releases/download/v%s/Tubifarry-v%s.net8.0.zip" $t.version $t.version) $t.url | quote }}
  volumeMounts:
    {{- include "kubarr-common.storage.configMount" (dict "root" . "mountPath" "/config") | nindent 4 }}
{{- end }}
{{- end -}}
