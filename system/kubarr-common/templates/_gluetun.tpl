{{/*
VPN pod fragments; include under containers, initContainers, and volumes respectively.
container accepts dict "root" . "appPort" <targetPort> "inputPorts" <additional comma-separated ports>
and optional "defaultUpCommand" <command>. All three helpers emit nothing when vpn.enabled is false.
*/}}

{{- define "kubarr-common.gluetun.container" -}}
{{- $root := .root -}}
{{- $vpn := $root.Values.vpn -}}
{{- if $vpn.enabled -}}
{{- $ports := list (toString .appPort) -}}
{{- range splitList "," (default "" .inputPorts) -}}
  {{- if trim . -}}
    {{- $ports = append $ports (trim .) -}}
  {{- end -}}
{{- end -}}
{{- $ports = append $ports "9999" -}}
{{- $ports = append $ports "8001" -}}
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
      value: ":8001"
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
    {{- with $vpn.extraEnv }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  ports:
    - name: gluetun-http
      containerPort: 8888
      protocol: TCP
    - name: gluetun-ctrl
      containerPort: 8001
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
{{- end -}}
{{- end }}

{{- define "kubarr-common.gluetun.initContainer" -}}
{{- $vpn := .root.Values.vpn -}}
{{- if $vpn.enabled -}}
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
{{- end -}}
{{- end }}
