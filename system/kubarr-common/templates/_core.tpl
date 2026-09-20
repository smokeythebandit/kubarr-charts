{{/* Generic names and labels. */}}

{{- define "kubarr-common.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kubarr-common.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "kubarr-common.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kubarr-common.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubarr-common.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "kubarr-common.name" . }}
{{- end }}

{{- define "kubarr-common.labels" -}}
helm.sh/chart: {{ include "kubarr-common.chart" . }}
{{ include "kubarr-common.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
managed-by: kubarr
{{- with index .Chart.Annotations "kubarr.io/category" }}
category: {{ . }}
{{- end }}
{{- end }}

{{- define "kubarr-common.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "kubarr-common.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Generic resources. */}}

{{- define "kubarr-common.serviceAccount" -}}
{{- $root := .root -}}
{{- if $root.Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "kubarr-common.serviceAccountName" $root }}
  namespace: {{ $root.Values.namespace.name }}
  labels:
    {{- include "kubarr-common.labels" $root | nindent 4 }}
  {{- with $root.Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{- end }}

{{- define "kubarr-common.service" -}}
{{- $root := .root -}}
{{- $app := .app -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "kubarr-common.fullname" $root }}
  namespace: {{ $root.Values.namespace.name }}
  labels:
    {{- include "kubarr-common.labels" $root | nindent 4 }}
  {{- if or $app.basePath $app.service.annotations }}
  annotations:
    {{- if $app.basePath }}
    kubarr.io/base-path: {{ $app.basePath | quote }}
    {{- end }}
    {{- with $app.service.annotations }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  {{- end }}
spec:
  type: {{ $app.service.type }}
  ports:
    - port: {{ $app.service.port }}
      targetPort: http
      protocol: TCP
      name: http
  selector:
    {{- include "kubarr-common.selectorLabels" $root | nindent 4 }}
{{- end }}

{{- define "kubarr-common.networkPolicy" -}}
{{- $root := .root -}}
{{- $app := .app -}}
{{- $exporterEnabled := and (hasKey $root.Values "exporter") $root.Values.exporter.enabled -}}
{{- if $root.Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ $root.Release.Name }}
  namespace: {{ $root.Values.namespace.name }}
  labels:
    {{- include "kubarr-common.labels" $root | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "kubarr-common.selectorLabels" $root | nindent 6 }}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    {{- range $root.Values.networkPolicy.ingressFrom }}
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ . }}
      ports:
        - protocol: TCP
          port: {{ $app.service.targetPort }}
        {{- if $exporterEnabled }}
        - protocol: TCP
          port: {{ $root.Values.exporter.port }}
        {{- end }}
    {{- end }}
  egress:
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
    {{- range $root.Values.networkPolicy.egressTo }}
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ . }}
      ports:
        - protocol: TCP
    {{- end }}
{{- end }}
{{- end }}

{{- define "kubarr-common.scheduling" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "kubarr-common.vpa" -}}
{{- $root := .root -}}
{{- with $root.Values.vpa -}}
{{- if .enabled }}
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: {{ include "kubarr-common.fullname" $root }}
  namespace: {{ $root.Values.namespace.name }}
  labels:
    {{- include "kubarr-common.labels" $root | nindent 4 }}
spec:
  targetRef:
    apiVersion: apps/v1
    kind: {{ default "Deployment" .targetKind }}
    name: {{ include "kubarr-common.fullname" $root }}
  updatePolicy:
    updateMode: {{ default "Auto" .updateMode | quote }}
  resourcePolicy:
    containerPolicies:
      - containerName: {{ default "*" .containerName | quote }}
        controlledResources:
          {{- toYaml (default (list "cpu" "memory") .controlledResources) | nindent 10 }}
        {{- with .minAllowed }}
        minAllowed:
          {{- toYaml . | nindent 10 }}
        {{- end }}
        {{- with .maxAllowed }}
        maxAllowed:
          {{- toYaml . | nindent 10 }}
        {{- end }}
        {{- with .controlledValues }}
        controlledValues: {{ . | quote }}
        {{- end }}
{{- end }}
{{- end }}
{{- end }}
