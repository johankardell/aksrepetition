[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9.-]+$')][string]$HostName,
    [Parameter(Mandatory)][ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')][string]$PrivateIp
)
$ErrorActionPreference = 'Stop'
$directory = '.\.artifacts\advanced'
New-Item $directory -ItemType Directory -Force | Out-Null
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
            - {name: POSTGRES_HOST, value: "$HostName"}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: orders_api}
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
            - {name: POSTGRES_HOST, value: "$HostName"}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: orders_worker}
            - {name: POSTGRES_PORT, value: "5432"}
"@ | Set-Content "$directory\database-patch.yaml"
@"
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: database-egress
  namespace: orders
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock: {cidr: $PrivateIp/32}
      ports: [{protocol: TCP, port: 5432}]
"@ | Set-Content "$directory\database-egress.yaml"
& .\ops\Add-GitOpsFile.ps1 -Source "$directory\database-patch.yaml" -Kind Patch -Namespace orders
& .\ops\Add-GitOpsFile.ps1 -Source "$directory\database-egress.yaml" -Kind Resource -Namespace orders
Write-Host 'Updated primary Git source only. Review, commit, push and reconcile orders.'
