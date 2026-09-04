{{- define "recoup.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "recoup.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "recoup.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "recoup.labels" -}}
app.kubernetes.io/name: {{ include "recoup.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "recoup.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "recoup.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "recoup.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "recoup.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "recoup.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s/%s" .Values.image.registry .Values.image.repository -}}:{{- $tag -}}
{{- end -}}
