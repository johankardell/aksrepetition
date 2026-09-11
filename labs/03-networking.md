# Lab 3 - Private dependencies, controlled egress and Gateway API

**Level:** advanced intermediate. **Scenario:** publish an internal business API over HTTPS while restricting dependencies and application traffic.

**Prerequisite:** labs 1-2 complete, a working private management path, Azure CLI 2.86+, and approved Azure Firewall/Private Link charges. The required ingress is internal; global/public publishing is reserved for lab 10.

## Directives

### 1. Create private service endpoints with DNS

```powershell
. .\scripts\Use-Lab.ps1
az deployment group create -g $Lab.ResourceGroup -n private-dependencies `
  -f .\infra\private-endpoints.bicep -p prefix=$Lab.Prefix location=$Lab.Location `
  vnetId=$Outputs.vnetId.value subnetId=$Outputs.endpointsSubnetId.value `
  acrId=$Outputs.acrId.value keyVaultId=$Outputs.keyVaultId.value serviceBusId=$Outputs.serviceBusId.value -o none
Resolve-DnsName "$($Lab.AcrName).azurecr.io"
Resolve-DnsName "$($Lab.KeyVaultName).vault.azure.net"
Resolve-DnsName "$($Lab.ServiceBusName).servicebus.windows.net"
```

Expected: CNAMEs to private-link zones and private endpoint IPs in the endpoint subnet. If using a management VNet, link these zones there or forward DNS to a resolver that can see them. ACR also has regional **data** endpoints; its private DNS zone group handles records beyond the registry login endpoint.

Only after private DNS/reachability works:

```powershell
az acr update -n $Lab.AcrName --public-network-enabled false -o none
az keyvault update -n $Lab.KeyVaultName --public-network-access Disabled -o none
az servicebus namespace update -g $Lab.ResourceGroup -n $Lab.ServiceBusName --public-network-access Disabled -o none
kubectl rollout restart deployment/order-api deployment/order-worker -n orders
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
```

Confirm new pods pull images, mount CSI, and process a fresh order. Public Azure management APIs still work; disabling data-plane public access does not disable ARM management.

### 2. Establish explicit egress via a peered firewall hub

Review `infra\firewall.bicep`: AKS FQDN tag, HTTPS identity/GitOps/monitoring allowances, and required control-plane network rules. The private API is not protected with public API authorized ranges; those are a different public endpoint design.

```powershell
az deployment group what-if -g $Lab.ResourceGroup -n egress `
  -f .\infra\firewall.bicep -p prefix=$Lab.Prefix location=$Lab.Location spokeName="$($Lab.Prefix)-vnet"
az deployment group create -g $Lab.ResourceGroup -n egress `
  -f .\infra\firewall.bicep -p prefix=$Lab.Prefix location=$Lab.Location spokeName="$($Lab.Prefix)-vnet" -o none
$egress = az deployment group show -g $Lab.ResourceGroup -n egress --query properties.outputs -o json | ConvertFrom-Json
az role assignment create --assignee-object-id $Outputs.clusterPrincipalId.value `
  --assignee-principal-type ServicePrincipal --role 'Network Contributor' --scope $egress.routeTableId.value -o none
az network vnet subnet update -g $Lab.ResourceGroup --vnet-name "$($Lab.Prefix)-vnet" `
  -n nodes --route-table $egress.routeTableId.value -o none
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --outbound-type userDefinedRouting -o none
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query networkProfile.outboundType -o tsv
kubectl get nodes
```

Expect `userDefinedRouting` and healthy nodes. Peering must allow forwarded traffic. The small single-frontend firewall is a bounded lab footprint; evaluate SNAT capacity and resilience separately for customers.

Enable firewall logs to the existing workspace:

```powershell
$firewallId = az network firewall show -g $Lab.ResourceGroup -n "$($Lab.Prefix)-fw" --query id -o tsv
az monitor diagnostic-settings categories list --resource $firewallId -o table
$logs = '[{"category":"AzureFirewallApplicationRule","enabled":true},{"category":"AzureFirewallNetworkRule","enabled":true}]'
az monitor diagnostic-settings create --name lab-firewall --resource $firewallId `
  --workspace $Outputs.workspaceId.value --logs $logs -o none
```

Inspect documented AKS and add-on outbound endpoints whenever enabling new features. Do not add `*` allow rules to hide an incomplete allowlist. The management/build host needs its own explicit outbound path; this route table is attached only to AKS nodes.

### 3. Enable the managed Gateway API implementation

```powershell
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --enable-gateway-api -o none
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --enable-app-routing-istio -o none
kubectl get crd gateways.gateway.networking.k8s.io
kubectl get gatewayclass approuting-istio
kubectl get pods -n aks-istio-system
```

Expected: managed standard-channel Gateway API CRDs and the `approuting-istio` GatewayClass. Do not install competing self-managed Gateway CRDs. This add-on is ingress-only, not a sidecar mesh, and cannot coexist with the managed Istio service-mesh add-on.

### 4. Terminate TLS at an internal gateway

Set `Hostname` in local settings to a lab DNS name. An owned public domain is not needed for this internal self-signed exercise; lab 10 needs one for trusted public TLS.

```powershell
. .\scripts\Use-Lab.ps1
.\scripts\New-LabCertificate.ps1 -Hostname $Lab.Hostname
kubectl create namespace gateway-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret tls orders-tls -n gateway-system `
  --cert .\rendered\certs\tls.crt --key .\rendered\certs\tls.key `
  --dry-run=client -o yaml | kubectl apply -f -
(Get-Content .\k8s\network\gateway.yaml -Raw).Replace('__HOSTNAME__', $Lab.Hostname) |
  Set-Content .\rendered\gateway.yaml -Encoding utf8
kubectl apply -f .\rendered\gateway.yaml
kubectl wait -n gateway-system gateway/orders-gateway --for=condition=Programmed --timeout=300s
kubectl get gateway -n gateway-system orders-gateway -o yaml
kubectl get httproute -n orders orders -o yaml
$ip = kubectl get gateway orders-gateway -n gateway-system -o jsonpath='{.status.addresses[0].value}'
curl.exe --fail --cacert .\rendered\certs\tls.crt `
  --resolve "$($Lab.Hostname):443:$ip" "https://$($Lab.Hostname)/readyz"
```

Expect private IP, accepted/resolved route references, and HTTP 200 with successful certificate verification. Do not replace trust verification with `-k`. Configure private DNS for normal clients if needed; `--resolve` intentionally keeps this lab independent of a DNS zone you own.

Platform team owns the Gateway and TLS lifecycle; app team owns its HTTPRoute. A namespace selector limits who can attach routes. For production, use a trusted certificate and the supported Key Vault/DNS integration or explicit certificate synchronization; do not keep PEM private keys in source control.

**Prepare the ordinary HTTPS client path used by labs 5-10.** `curl --resolve --cacert` does not configure DNS or trust for PowerShell, so complete both before calling the later load scripts. Create a private zone at the exact application hostname to avoid shadowing unrelated parent-domain records:

```powershell
az network private-dns zone create -g $Lab.ResourceGroup -n $Lab.Hostname -o none
az network private-dns link vnet create -g $Lab.ResourceGroup --zone-name $Lab.Hostname `
  -n orders-ingress --virtual-network $Outputs.vnetId.value --registration-enabled false -o none
az network private-dns record-set a add-record -g $Lab.ResourceGroup `
  --zone-name $Lab.Hostname --record-set-name '@' --ipv4-address $ip -o none
Resolve-DnsName $Lab.Hostname
```

If your management client uses a separate VNet or corporate DNS, add its private-zone link/conditional forwarding just as for the private AKS API. Resolution must return the gateway IP; `--resolve` must no longer be necessary.

On the **Windows management workstation**, explicitly trust this lab-generated certificate in the current user's certificate store (not machine-wide), then prove PowerShell HTTPS works:

```powershell
if (-not $IsWindows) { throw 'Use your approved OS trust-store procedure on a non-Windows management host, then verify HTTPS without bypassing validation.' }
$trusted = Import-Certificate -FilePath .\rendered\certs\tls.crt -CertStoreLocation Cert:\CurrentUser\Root
$trusted.Thumbprint | Set-Content .\rendered\certs\trusted-thumbprint.txt
Invoke-RestMethod "https://$($Lab.Hostname)/readyz"
```

Trust only the certificate you just generated in this disposable lab; never import an arbitrary supplied root. The self-signed certificate expires after 14 days. Renew the certificate, update the Kubernetes TLS Secret and replace the old current-user trust entry if resuming after expiry. A trusted enterprise certificate issued for the hostname avoids this local lab trust setup. Do not add certificate-validation bypasses to the load generator.

### 5. Apply least-required application network access

```powershell
Copy-Item .\k8s\network\policies.yaml .\rendered\base\policies.yaml
$k = Get-Content .\rendered\base\kustomization.yaml -Raw
if ($k -notmatch 'policies.yaml') {
  $k = $k.Replace('  - worker.yaml', "  - worker.yaml`n  - policies.yaml")
  Set-Content .\rendered\base\kustomization.yaml $k -Encoding utf8
}
kubectl apply -k .\rendered\base
kubectl get networkpolicy -n orders
curl.exe --fail --cacert .\rendered\certs\tls.crt `
  --resolve "$($Lab.Hostname):443:$ip" "https://$($Lab.Hostname)/readyz"
```

Default-deny covers ingress and egress. DNS to kube-system and TCP 443 are allowed; **the firewall** enforces public HTTPS destinations. Private endpoint routes bypass the internet firewall as designed. A TCP 443 allow rule alone is not destination-level isolation.

Prove the full approved request path still processes an order:

```powershell
$payload = @{ id = "private-$([guid]::NewGuid().ToString('N'))"; item = 'synthetic-private-widget' } | ConvertTo-Json -Compress
curl.exe --fail --cacert .\rendered\certs\tls.crt `
  --resolve "$($Lab.Hostname):443:$ip" -H 'Content-Type: application/json' `
  --data-raw $payload "https://$($Lab.Hostname)/orders"
kubectl logs -n orders deployment/order-worker --since=5m
```

Test that the worker cannot call the API directly:

```powershell
$worker = kubectl get pod -n orders -l app=order-worker -o jsonpath='{.items[0].metadata.name}'
kubectl exec -n orders $worker -- python -c `
  "import urllib.request; urllib.request.urlopen('http://order-api/readyz', timeout=5)"
```

Expected failure: timeout/denial, not HTTP success. Run expected-failure commands separately because native errors stop the current invocation.

Test unapproved outbound HTTPS from an API pod:

```powershell
$pod = kubectl get pod -n orders -l app=order-api -o jsonpath='{.items[0].metadata.name}'
kubectl exec -n orders $pod -- python -c `
  "import urllib.request; urllib.request.urlopen('https://example.org', timeout=10)"
```

Expect firewall denial and a corresponding log; a generic failure without destination/rule evidence is not proof of enforcement. Confirm approved Service Bus processing still works through the gateway.

### 6. Break and repair private DNS without changing production-like records

Read the healthy record, then use a short-lived pod-local host override to simulate incorrect DNS on a disposable copy rather than corrupting a shared private zone:

```powershell
$patch = @{
  spec = @{ template = @{ spec = @{ hostAliases = @(
    @{ ip = '192.0.2.1'; hostnames = @("$($Lab.ServiceBusName).servicebus.windows.net") }
  ) } } }
} | ConvertTo-Json -Depth 8 -Compress
kubectl patch deployment order-worker -n orders --type merge -p $patch
kubectl logs -n orders deployment/order-worker --since=5m
kubectl describe deployment order-worker -n orders
```

Inspect `/etc/hosts` inside the current worker pod and compare with `Resolve-DnsName` on the management host. The pod override should produce a Service Bus timeout while the administrator still resolves the real private endpoint. This illustrates that resolution context matters; it is not an authoritative DNS-zone outage.

**Solution:**

```powershell
kubectl patch deployment order-worker -n orders --type merge `
  -p '{"spec":{"template":{"spec":{"hostAliases":null}}}}'
kubectl rollout status deployment/order-worker -n orders --timeout=300s
```

Submit a new order, confirm worker processing, and check for accumulated retries/dead-letter messages.

## Exit evidence and customer discussion

Keep private resolutions, disabled public data endpoints, the effective outbound type, accepted TLS route, negative east-west/outbound evidence, firewall log correlation and restored processing.

| Customer question | Model answer |
|---|---|
| Does private AKS mean the app cannot be public? | No. The control-plane endpoint and application ingress are separate design choices. |
| Is a NAT gateway a firewall? | NAT provides address translation and SNAT capacity, not an application destination policy. |
| Why Cilium Overlay? | It conserves VNet IPs and provides the managed data plane. Direct pod-IP reachability may justify Azure CNI Pod Subnet instead. |
| Why Gateway API? | It separates infrastructure and route ownership and is the modern managed ingress path. Application routing and Application Gateway for Containers have different operational/support characteristics. |
| Does this give WAF, API authentication or service mTLS? | No. Gateway routing/TLS, WAF, identity-aware API controls and east-west mesh policy solve distinct problems. |

## Cleanup and references

Remove the host override, keep private endpoints/firewall/policies/gateway and private ingress DNS for later labs, and protect/delete local certificate files at final teardown. At final teardown, remove the specific imported lab certificate from `Cert:\CurrentUser\Root` using the thumbprint saved in `rendered\certs\trusted-thumbprint.txt`; do not remove unrelated trusted certificates. Do not redeploy the lab 1 bootstrap template over these network changes. Keep `rendered\base` intact for Flux adoption and `rendered\gateway.yaml` as the separately owned platform ingress configuration.

Sources reviewed 2026-09-10: [AKS firewall egress](https://learn.microsoft.com/azure/aks/limit-egress-traffic), [required outbound rules](https://learn.microsoft.com/azure/aks/outbound-rules-control-egress), [managed Gateway API](https://learn.microsoft.com/azure/aks/managed-gateway-api), [application routing Gateway API](https://learn.microsoft.com/azure/aks/app-routing-gateway-api), [Gateway TLS](https://learn.microsoft.com/azure/aks/app-routing-gateway-api-tls), [ACR Private Link](https://learn.microsoft.com/azure/container-registry/container-registry-private-link).
