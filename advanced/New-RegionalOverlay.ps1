[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][string]$RegistryServer,
    [Parameter(Mandatory)][ValidatePattern('^sha256:[a-f0-9]{64}$')][string]$ImageDigest,
    [Parameter(Mandatory)][string]$ServiceBusNamespace,
    [Parameter(Mandatory)][string]$ApiClientId,
    [Parameter(Mandatory)][string]$WorkerClientId,
    [Parameter(Mandatory)][string]$PostgresHost,
    [Parameter(Mandatory)][string]$PostgresPrivateIp,
    [string]$ApiRole = 'orders_api_dr',
    [string]$WorkerRole = 'orders_worker_dr',
    [ValidateRange(0,10)][int]$ApiReplicas = 0,
    [ValidateRange(0,10)][int]$WorkerReplicas = 0
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
foreach ($value in @($RegistryServer,$ServiceBusNamespace,$ApiClientId,$WorkerClientId,$PostgresHost,$PostgresPrivateIp,$ApiRole,$WorkerRole)) {
    if ($value -match "[`r`n'`"]") { throw 'Use single-line unquoted parameter values.' }
}
New-Item "$OutputDirectory\base" -ItemType Directory -Force | Out-Null
Get-ChildItem .\k8s\base\*.yaml | ForEach-Object {
    $text = Get-Content $_.FullName -Raw
    $text = $text.Replace('__SERVICEBUS_NAMESPACE__',$ServiceBusNamespace).
        Replace('__API_CLIENT_ID__',$ApiClientId).Replace('__WORKER_CLIENT_ID__',$WorkerClientId).
        Replace('__REGISTRY__',$RegistryServer).Replace('__IMAGE_TAG__','v1')
    if ($_.Name -eq 'kustomization.yaml') {
        $text = $text -replace '(?m)^  - namespace[.]yaml\r?\n', ''
    }
    Set-Content (Join-Path "$OutputDirectory\base" $_.Name) $text
}
@"
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - base
  - network.yaml
patches:
  - path: database.yaml
replicas:
  - name: order-api
    count: $ApiReplicas
  - name: order-worker
    count: $WorkerReplicas
images:
  - name: $RegistryServer/order-app
    newName: $RegistryServer/order-app
    digest: $ImageDigest
"@ | Set-Content "$OutputDirectory\kustomization.yaml"
@"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-api
  namespace: orders
spec:
  template:
    spec:
      containers:
        - name: api
          env:
            - {name: POSTGRES_HOST, value: "$PostgresHost"}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: "$ApiRole"}
            - {name: POSTGRES_PORT, value: "5432"}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-worker
  namespace: orders
spec:
  template:
    spec:
      containers:
        - name: worker
          env:
            - {name: POSTGRES_HOST, value: "$PostgresHost"}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: "$WorkerRole"}
            - {name: POSTGRES_PORT, value: "5432"}
"@ | Set-Content "$OutputDirectory\database.yaml"
@"
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: regional-default-deny
  namespace: orders
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: regional-dependencies
  namespace: orders
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: kube-system}
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
    - to:
        - ipBlock: {cidr: 0.0.0.0/0}
      ports: [{protocol: TCP, port: 443}]
    - to:
        - ipBlock: {cidr: $PostgresPrivateIp/32}
      ports: [{protocol: TCP, port: 5432}]
    - to:
        - podSelector:
            matchLabels: {app: order-api}
      ports: [{protocol: TCP, port: 8080}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: origin-ingress
  namespace: orders
spec:
  podSelector:
    matchLabels: {app: regional-origin}
  policyTypes: [Ingress]
  ingress:
    - ports: [{protocol: TCP, port: 8443}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: regional-api
  namespace: orders
spec:
  podSelector:
    matchLabels: {app: order-api}
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels: {app: regional-origin}
      ports: [{protocol: TCP, port: 8080}]
"@ | Set-Content "$OutputDirectory\network.yaml"
$rendered = (kubectl kustomize $OutputDirectory) -join "`n"
if ($rendered -match '(?m)^kind: (HorizontalPodAutoscaler|ScaledObject|TriggerAuthentication|ClusterTriggerAuthentication)\s*$' -or
    $rendered -match '__SCALER_CLIENT_ID__') {
    throw 'The fixed-replica DR overlay must not inherit autoscalers or scaler authentication. Keep scaling assets out of k8s/base.'
}
Write-Host "Created cold application overlay at $OutputDirectory. No HPA/KEDA is included."
