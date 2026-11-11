{{/*
VPN pod fragments; include under containers, initContainers, and volumes respectively.
container accepts dict "root" . "appPort" <targetPort> "inputPorts" <additional comma-separated ports>
optional "defaultUpCommand" <command>, and optional "p2p" true (torrent clients; picks P2P-capable servers).
The VPN init container helper takes the same "p2p" key and also emits the picker init container. All three helpers emit nothing when vpn.enabled is false.
*/}}

{{/*
Server picker settings (the picker is only enabled with vpn.killSwitch: a reconnect without the firewall would leak app traffic), read with defaults so releases upgraded with
--reuse-values (which drops new chart defaults, leaving vpn.picker absent) still
render a working picker. Emits JSON; decode with fromJson.
*/}}
{{- define "kubarr-common.gluetun.pickerSettings" -}}
{{- $p := default dict .vpn.picker -}}
{{- $image := default dict (get $p "image") -}}
{{- dict
  "enabled" (and (dig "enabled" true $p) (default false .vpn.killSwitch))
  "shortlist" (dig "shortlist" 5 $p)
  "sample" (dig "sample" 40 $p)
  "initBudget" (dig "initBudget" "60s" $p)
  "testSeconds" (dig "testSeconds" 8 $p)
  "testEndpoints" (dig "testEndpoints" (list "https://proof.ovh.net/files/100Mb.dat" "http://speedtest.tele2.net/100MB.zip") $p)
  "interval" (dig "interval" "15m" $p)
  "degradeThreshold" (dig "degradeThreshold" 0.4 $p)
  "degradeChecks" (dig "degradeChecks" 3 $p)
  "cooldown" (dig "cooldown" "6h" $p)
  "switchWhileRunning" (dig "switchWhileRunning" true $p)
  "image" (printf "%s:%s" (dig "repository" "ghcr.io/smokeythebandit/kubarr-vpn-picker" $image) (dig "tag" "26e9358" $image))
  "pullPolicy" (dig "pullPolicy" "IfNotPresent" $image)
  "resources" (dig "resources" (dict "requests" (dict "cpu" "10m" "memory" "32Mi") "limits" (dict "cpu" "200m" "memory" "128Mi")) $p)
  | toJson -}}
{{- end }}

{{/*
Picker container body shared by the init container and the sidecar. Takes
dict "root" . "p2p" <bool> "mode" init|run. Credentials come from the VPN Secret
key by key (never envFrom) so the WireGuard private key stays out of the picker.
*/}}
{{- define "kubarr-common.gluetun.pickerContainer" -}}
{{- $vpn := .root.Values.vpn -}}
{{- $s := include "kubarr-common.gluetun.pickerSettings" (dict "vpn" $vpn) | fromJson -}}
{{- $init := eq .mode "init" -}}
- name: {{ ternary "vpn-picker-init" "vpn-picker" $init }}
  image: {{ $s.image | quote }}
  imagePullPolicy: {{ $s.pullPolicy }}
  args: [{{ .mode | quote }}]
  securityContext:
    {{- if $init }}
    # Raw ICMP needs an effective NET_RAW, which a non-root user never gets,
    # so only the init container runs as root (all other capabilities dropped).
    runAsUser: 0
    runAsGroup: 0
    runAsNonRoot: false
    {{- else }}
    runAsUser: 1000
    runAsGroup: 1000
    runAsNonRoot: true
    {{- end }}
    readOnlyRootFilesystem: true
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
      {{- if $init }}
      add:
        - NET_RAW
      {{- end }}
  volumeMounts:
    - name: picker
      mountPath: /picker
  env:
    - name: PICKER_STATE_DIR
      value: /picker
    - name: PICKER_P2P
      value: {{ ternary "true" "false" (default false .p2p) | quote }}
    - name: PICKER_SHORTLIST
      value: {{ $s.shortlist | int | quote }}
    - name: PICKER_SAMPLE
      value: {{ $s.sample | int | quote }}
    - name: PICKER_INIT_BUDGET
      value: {{ $s.initBudget | quote }}
    - name: PICKER_TEST_SECONDS
      value: {{ $s.testSeconds | int | quote }}
    - name: PICKER_TEST_ENDPOINTS
      value: {{ join "," $s.testEndpoints | quote }}
    - name: PICKER_INTERVAL
      value: {{ $s.interval | quote }}
    - name: PICKER_DEGRADE_THRESHOLD
      value: {{ $s.degradeThreshold | quote }}
    - name: PICKER_DEGRADE_CHECKS
      value: {{ $s.degradeChecks | int | quote }}
    - name: PICKER_COOLDOWN
      value: {{ $s.cooldown | quote }}
    - name: PICKER_SWITCH_WHILE_RUNNING
      value: {{ $s.switchWhileRunning | toString | quote }}
    - name: VPN_PORT_FORWARDING
      value: {{ ternary "on" "off" (dig "enabled" false (default dict $vpn.portForwarding)) | quote }}
    {{- range list "VPN_SERVICE_PROVIDER" "VPN_TYPE" "SERVER_COUNTRIES" "SERVER_CITIES" "SERVER_HOSTNAMES" }}
    - name: {{ . }}
      valueFrom:
        secretKeyRef:
          name: {{ $vpn.secretName }}
          key: {{ . }}
          optional: true
    {{- end }}
  {{- if not $init }}
  resources:
    {{- toYaml $s.resources | nindent 4 }}
  {{- end }}
{{- end }}

{{/*
Picker init container: chooses a VPN server before gluetun starts. Runs before
gluetun's firewall exists, so it can reach the provider and test servers directly.
Accepts dict "root" . and optional "p2p" true. Emits nothing unless vpn.enabled
and vpn.picker.enabled.
*/}}
{{- define "kubarr-common.gluetun.pickerInit" -}}
{{- $vpn := .root.Values.vpn -}}
{{- if $vpn.enabled -}}
{{- if (include "kubarr-common.gluetun.pickerSettings" (dict "vpn" $vpn) | fromJson).enabled -}}
{{- include "kubarr-common.gluetun.pickerContainer" (dict "root" .root "p2p" .p2p "mode" "init") -}}
{{- end -}}
{{- end -}}
{{- end }}

{{- define "kubarr-common.gluetun.container" -}}
{{- $root := .root -}}
{{- $vpn := $root.Values.vpn -}}
{{- if $vpn.enabled -}}
{{- $picker := (include "kubarr-common.gluetun.pickerSettings" (dict "vpn" $vpn) | fromJson).enabled -}}
{{- $ports := list (toString .appPort) -}}
{{- range splitList "," (default "" .inputPorts) -}}
  {{- if trim . -}}
    {{- $ports = append $ports (trim .) -}}
  {{- end -}}
{{- end -}}
{{- $ports = append $ports "9999" -}}
{{- $control := toString (default 8001 $vpn.controlPort) -}}
{{- $ports = append $ports $control -}}
- name: gluetun
  image: "{{ $vpn.image.repository }}:{{ $vpn.image.tag }}"
  imagePullPolicy: {{ $vpn.image.pullPolicy }}
  securityContext:
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
      add:
        - NET_ADMIN
        - CHOWN
  volumeMounts:
    - name: dev-net-tun
      mountPath: /dev/net/tun
    - name: gluetun-tmp
      mountPath: /tmp/gluetun
  envFrom:
    - secretRef:
        name: {{ $vpn.secretName }}
  env:
    {{- with $vpn.firewallOutboundSubnets }}
    - name: FIREWALL_OUTBOUND_SUBNETS
      value: {{ . | quote }}
    {{- end }}
    - name: FIREWALL
      value: {{ ternary "on" "off" $vpn.killSwitch | quote }}
    {{- if $vpn.killSwitch }}
    - name: FIREWALL_INPUT_PORTS
      value: {{ join "," (uniq $ports) | quote }}
    {{- end }}
    - name: HEALTH_SERVER_ADDRESS
      value: ":9999"
    - name: HTTP_CONTROL_SERVER_ADDRESS
      value: {{ printf ":%s" $control | quote }}
    {{- with $vpn.portForwarding }}
    {{- if .enabled }}
    - name: VPN_PORT_FORWARDING
      value: "on"
    - name: PORT_FORWARD_ONLY
      value: "on"
    {{- with (default $.defaultUpCommand .upCommand) }}
    - name: VPN_PORT_FORWARDING_UP_COMMAND
      value: {{ . | quote }}
    {{- end }}
    {{- end }}
    {{- end }}
    {{- if $picker }}
    # The picker switches servers through gluetun's control API; the network
    # policy is what protects it.
    - name: HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE
      value: '{"auth":"none"}'
    {{- end }}
    {{- with $vpn.extraEnv }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  ports:
    - name: gluetun-http
      containerPort: 8888
      protocol: TCP
    - name: gluetun-ctrl
      containerPort: {{ $control }}
      protocol: TCP
  livenessProbe:
    httpGet:
      path: /
      port: 9999
    initialDelaySeconds: 60
    periodSeconds: 30
    timeoutSeconds: 15
    failureThreshold: 5
  readinessProbe:
    httpGet:
      path: /
      port: 9999
    initialDelaySeconds: 30
    periodSeconds: 15
    timeoutSeconds: 10
    failureThreshold: 5
  resources:
    {{- toYaml $vpn.resources | nindent 4 }}
{{- if $picker }}
{{ include "kubarr-common.gluetun.pickerContainer" (dict "root" $root "p2p" .p2p "mode" "run") }}
{{- end }}
{{- end -}}
{{- end }}

{{- define "kubarr-common.gluetun.initContainer" -}}
{{- $vpn := .root.Values.vpn -}}
{{- if $vpn.enabled -}}
{{- $pickerInit := include "kubarr-common.gluetun.pickerInit" . -}}
{{- if $pickerInit }}{{ $pickerInit }}
{{ end -}}
- name: gluetun-tmp-permissions
  image: "{{ $vpn.image.repository }}:{{ $vpn.image.tag }}"
  imagePullPolicy: {{ $vpn.image.pullPolicy }}
  command: ["/bin/sh", "-c", "chmod 0700 /tmp/gluetun"]
  securityContext:
    runAsUser: 0
    runAsNonRoot: false
    allowPrivilegeEscalation: false
    capabilities:
      drop:
        - ALL
  volumeMounts:
    - name: gluetun-tmp
      mountPath: /tmp/gluetun
{{- end -}}
{{- end }}

{{- define "kubarr-common.gluetun.volumes" -}}
{{- if .root.Values.vpn.enabled -}}
- name: dev-net-tun
  hostPath:
    path: /dev/net/tun
    type: CharDevice
- name: gluetun-tmp
  emptyDir: {}
{{- if (include "kubarr-common.gluetun.pickerSettings" (dict "vpn" .root.Values.vpn) | fromJson).enabled }}
- name: picker
  emptyDir: {}
{{- end }}
{{- end -}}
{{- end }}

{{/*
Pod DNS options for VPN pods; include at pod spec level. With VPN on, the pod
resolves through gluetun, which forwards every query through the tunnel, so
Kubernetes' default ndots:5 made each external name try three cluster search
suffixes first, each hanging ~2s at the VPN's resolver (6-12s per lookup).
ndots:1 sends dotted names as typed; in-cluster names used from VPN pods are
fully qualified. Emits nothing when vpn.enabled is false.
*/}}
{{- define "kubarr-common.gluetun.dnsConfig" -}}
{{- if .root.Values.vpn.enabled -}}
dnsConfig:
  options:
    - name: ndots
      value: "1"
{{- end -}}
{{- end }}
