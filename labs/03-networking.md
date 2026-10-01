# Lab 3 - Private dependencies, controlled egress and Gateway API

**Level:** advanced intermediate. **Scenario:** publish an internal business API over HTTPS while restricting dependencies and application traffic.

**Prerequisite:** labs 1-2 complete, a working private management path, Azure CLI 2.86+, and approved Azure Firewall/Private Link charges. The required ingress is internal; global/public publishing is reserved for lab 10.

## Directives

### 1. Create private service endpoints with DNS

**Task:** establish private endpoints and DNS for ACR, Key Vault and Service Bus, then disable their public data endpoints and demonstrate fresh workload access. Do not disable public access until private reachability works from the management/build host and cluster.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az deployment group create -g "$(lab_value ResourceGroup)" -n private-dependencies \
  -f ./infra/private-endpoints.bicep -p "prefix=$(lab_value Prefix)" "location=$(lab_value Location)" \
  "vnetId=$(output_value vnetId)" "subnetId=$(output_value endpointsSubnetId)" \
  "acrId=$(output_value acrId)" "keyVaultId=$(output_value keyVaultId)" "serviceBusId=$(output_value serviceBusId)" -o none
dig "$(lab_value AcrName).azurecr.io"
dig "$(lab_value KeyVaultName).vault.azure.net"
dig "$(lab_value ServiceBusName).servicebus.windows.net"
```

Expected: CNAMEs to private-link zones and private endpoint IPs in the endpoint subnet. If using a management VNet, link these zones there or forward DNS to a resolver that can see them. ACR also has regional **data** endpoints; its private DNS zone group handles records beyond the registry login endpoint.

Only after private DNS/reachability works:

```bash
az acr update -n "$(lab_value AcrName)" --public-network-enabled false -o none
az keyvault update -n "$(lab_value KeyVaultName)" --public-network-access Disabled -o none
az servicebus namespace update -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" --public-network-access Disabled -o none
kubectl rollout restart deployment/order-api deployment/order-worker -n orders
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
```

Confirm new pods pull images, mount CSI, and process a fresh order. Public Azure management APIs still work; disabling data-plane public access does not disable ARM management.

</details>

### 2. Establish explicit egress via a peered firewall hub

**Task:** review and deploy the bounded firewall hub, route AKS egress through it, and enable diagnostic evidence. Approve the standing firewall cost first; do not use wildcard allow rules or redeploy the original foundation over the progressed cluster.

<details>
<summary>Solution</summary>

Review `infra/firewall.bicep`: AKS FQDN tag, HTTPS identity/GitOps/monitoring allowances, and required control-plane network rules. The private API is not protected with public API authorized ranges; those are a different public endpoint design.

```bash
az deployment group what-if -g "$(lab_value ResourceGroup)" -n egress \
  -f ./infra/firewall.bicep -p "prefix=$(lab_value Prefix)" "location=$(lab_value Location)" "spokeName=$(lab_value Prefix)-vnet"
az deployment group create -g "$(lab_value ResourceGroup)" -n egress \
  -f ./infra/firewall.bicep -p "prefix=$(lab_value Prefix)" "location=$(lab_value Location)" "spokeName=$(lab_value Prefix)-vnet" -o none
egress=$(az deployment group show -g "$(lab_value ResourceGroup)" -n egress --query properties.outputs -o json)
az role assignment create --assignee-object-id "$(output_value clusterPrincipalId)" \
  --assignee-principal-type ServicePrincipal --role 'Network Contributor' --scope "$(jq -r '.routeTableId.value' <<< "$egress")" -o none
az network vnet subnet update -g "$(lab_value ResourceGroup)" --vnet-name "$(lab_value Prefix)-vnet" \
  -n nodes --route-table "$(jq -r '.routeTableId.value' <<< "$egress")" -o none
az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --outbound-type userDefinedRouting -o none
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --query networkProfile.outboundType -o tsv
kubectl get nodes
```

Expect `userDefinedRouting` and healthy nodes. Peering must allow forwarded traffic. The small single-frontend firewall is a bounded lab footprint; evaluate SNAT capacity and resilience separately for customers.

Enable firewall logs to the existing workspace:

```bash
firewallId=$(az network firewall show -g "$(lab_value ResourceGroup)" -n "$(lab_value Prefix)-fw" --query id -o tsv)
az monitor diagnostic-settings categories list --resource "$firewallId" -o table
logs='[{"category":"AzureFirewallApplicationRule","enabled":true},{"category":"AzureFirewallNetworkRule","enabled":true}]'
az monitor diagnostic-settings create --name lab-firewall --resource "$firewallId" \
  --workspace "$(output_value workspaceId)" --logs "$logs" -o none
```

Inspect documented AKS and add-on outbound endpoints whenever enabling new features. Do not add `*` allow rules to hide an incomplete allowlist. The management/build host needs its own explicit outbound path; this route table is attached only to AKS nodes.

</details>

### 3. Enable the managed Gateway API implementation

**Task:** enable the supported managed Gateway API and ingress implementation, and identify evidence that both are ready. Do not install competing CRDs or combine this ingress add-on with the managed Istio service-mesh add-on.

<details>
<summary>Solution</summary>

```bash
az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --enable-gateway-api -o none
az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --enable-app-routing-istio -o none
kubectl get crd gateways.gateway.networking.k8s.io
kubectl get gatewayclass approuting-istio
kubectl get pods -n aks-istio-system
```

Expected: managed standard-channel Gateway API CRDs and the `approuting-istio` GatewayClass. Do not install competing self-managed Gateway CRDs. This add-on is ingress-only, not a sidecar mesh, and cannot coexist with the managed Istio service-mesh add-on.

</details>

### 4. Terminate TLS at an internal gateway

**Task:** publish the API at an internal HTTPS gateway, verify route attachment and certificate validation, then configure ordinary private DNS and explicit Linux client trust for later exercises. Keep ingress internal, never commit TLS private keys, and never bypass certificate validation. Trust only your own lab-generated certificate in a dedicated local CA bundle; record its SHA-256 fingerprint for cleanup.

<details>
<summary>Solution</summary>

Set `Hostname` in local settings to a lab DNS name. An owned public domain is not needed for this internal self-signed exercise; lab 10 needs one for trusted public TLS.

```bash
source ./scripts/use-lab.sh
bash ./scripts/new-lab-certificate.sh --hostname "$(lab_value Hostname)"
kubectl create namespace gateway-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret tls orders-tls -n gateway-system \
  --cert ./rendered/certs/tls.crt --key ./rendered/certs/tls.key \
  --dry-run=client -o yaml | kubectl apply -f -
gateway=$(< ./k8s/network/gateway.yaml)
gateway=${gateway//__HOSTNAME__/$(lab_value Hostname)}
if grep -Eq '__[A-Z0-9_]+__' <<< "$gateway"; then
  printf '%s\n' 'Unresolved gateway token.' >&2
  exit 1
fi
printf '%s\n' "$gateway" > ./rendered/gateway.yaml
kubectl apply -f ./rendered/gateway.yaml
kubectl wait -n gateway-system gateway/orders-gateway --for=condition=Programmed --timeout=300s
kubectl get gateway -n gateway-system orders-gateway -o yaml
kubectl get httproute -n orders orders -o yaml
ip=$(kubectl get gateway orders-gateway -n gateway-system -o jsonpath='{.status.addresses[0].value}')
curl --fail --show-error --cacert ./rendered/certs/tls.crt \
  --resolve "$(lab_value Hostname):443:$ip" "https://$(lab_value Hostname)/readyz"
```

Expect private IP, accepted/resolved route references, and HTTP 200 with successful certificate verification. Do not replace trust verification with `-k`. Configure private DNS for normal clients if needed; `--resolve` intentionally keeps this lab independent of a DNS zone you own.

Platform team owns the Gateway and TLS lifecycle; app team owns its HTTPRoute. A namespace selector limits who can attach routes. For production, use a trusted certificate and the supported Key Vault/DNS integration or explicit certificate synchronization; do not keep PEM private keys in source control.

**Prepare the ordinary HTTPS client path used by labs 5-10.** `curl --resolve --cacert` does not configure DNS or persistent client trust, so complete both before calling the later load scripts. Create a private zone at the exact application hostname to avoid shadowing unrelated parent-domain records:

```bash
az network private-dns zone create -g "$(lab_value ResourceGroup)" -n "$(lab_value Hostname)" -o none
az network private-dns link vnet create -g "$(lab_value ResourceGroup)" --zone-name "$(lab_value Hostname)" \
  -n orders-ingress --virtual-network "$(output_value vnetId)" --registration-enabled false -o none
az network private-dns record-set a add-record -g "$(lab_value ResourceGroup)" \
  --zone-name "$(lab_value Hostname)" --record-set-name '@' --ipv4-address "$ip" -o none
dig "$(lab_value Hostname)"
getent ahostsv4 "$(lab_value Hostname)"
```

If your management client uses a separate VNet or corporate DNS, add its private-zone link/conditional forwarding just as for the private AKS API. Resolution must return the gateway IP; `--resolve` must no longer be necessary.

On the **Linux management host**, inspect the generated certificate's subject, SAN, validity and fingerprint. Build a user-owned bundle that retains the system roots and adds only this lab certificate; do not install it machine-wide. The standard system bundle paths below cover Debian/Ubuntu and RHEL-family hosts; on another distribution, select its approved PEM CA bundle explicitly.

```bash
openssl x509 -in ./rendered/certs/tls.crt -noout -subject -dates -ext subjectAltName -fingerprint -sha256
openssl x509 -in ./rendered/certs/tls.crt -noout -checkhost "$(lab_value Hostname)"
if [[ -f /etc/ssl/certs/ca-certificates.crt ]]; then
  SystemCaBundle=/etc/ssl/certs/ca-certificates.crt
elif [[ -f /etc/pki/tls/certs/ca-bundle.crt ]]; then
  SystemCaBundle=/etc/pki/tls/certs/ca-bundle.crt
else
  printf '%s\n' 'Select the approved system PEM CA bundle for this Linux distribution before proceeding.' >&2
  exit 1
fi
umask 077
cat "$SystemCaBundle" ./rendered/certs/tls.crt > ./rendered/certs/lab-ca-bundle.pem
openssl x509 -in ./rendered/certs/tls.crt -noout -fingerprint -sha256 \
  > ./rendered/certs/trusted-fingerprint.txt
export CURL_CA_BUNDLE="$Root/rendered/certs/lab-ca-bundle.pem"
export SSL_CERT_FILE="$CURL_CA_BUNDLE"
export REQUESTS_CA_BUNDLE="$CURL_CA_BUNDLE"
curl --fail --show-error "https://$(lab_value Hostname)/readyz"
```

Trust only the certificate you just generated in this disposable lab; never add an arbitrary supplied root. In each new terminal, source `use-lab.sh` and re-export the three absolute CA bundle paths above before using ordinary HTTPS clients or load scripts. The bundle is explicit client trust, not a system-wide installation; clients that ignore these variables need an approved client-specific CA setting. The self-signed certificate expires after 14 days. Renew it, update the Kubernetes TLS Secret and rebuild the bundle/fingerprint record if resuming after expiry. A trusted enterprise certificate issued for the hostname avoids this local lab trust setup. Do not add certificate-validation bypasses to the load generator.

</details>

### 5. Apply least-required application network access

**Task:** apply default-deny application policies with only the required allowances. Prove approved order processing, denied worker-to-API traffic and denied unapproved public HTTPS, attributing each denial to the right control. Keep the extended rendered base for lab 4.

<details>
<summary>Solution</summary>

```bash
cp ./k8s/network/policies.yaml ./rendered/base/policies.yaml
k=$(< ./rendered/base/kustomization.yaml)
if ! grep -Fq 'policies.yaml' <<< "$k"; then
  if ! grep -Fxq '  - worker.yaml' <<< "$k"; then
    printf '%s\n' 'Expected worker resource entry in the accumulated Kustomization.' >&2
    exit 1
  fi
  k=${k/'  - worker.yaml'/$'  - worker.yaml\n  - policies.yaml'}
  printf '%s\n' "$k" > ./rendered/base/kustomization.yaml
fi
kubectl kustomize ./rendered/base > /dev/null
kubectl apply -k ./rendered/base
kubectl get networkpolicy -n orders
curl --fail --show-error --cacert ./rendered/certs/tls.crt \
  --resolve "$(lab_value Hostname):443:$ip" "https://$(lab_value Hostname)/readyz"
```

Default-deny covers ingress and egress. DNS to kube-system and TCP 443 are allowed; **the firewall** enforces public HTTPS destinations. Private endpoint routes bypass the internet firewall as designed. A TCP 443 allow rule alone is not destination-level isolation.

Prove the full approved request path still processes an order:

```bash
OrderId="private-$(openssl rand -hex 16)"
payload=$(jq -nc --arg id "$OrderId" '{id:$id,item:"synthetic-private-widget"}')
curl --fail --show-error --cacert ./rendered/certs/tls.crt \
  --resolve "$(lab_value Hostname):443:$ip" -H 'Content-Type: application/json' \
  --data-raw "$payload" "https://$(lab_value Hostname)/orders"
kubectl logs -n orders deployment/order-worker --since=5m
```

Test that the worker cannot call the API directly:

```bash
worker=$(kubectl get pod -n orders -l app=order-worker -o jsonpath='{.items[0].metadata.name}')
Status=0
Denial=$(kubectl exec -n orders "$worker" -- python -c \
  "import urllib.request; urllib.request.urlopen('http://order-api/readyz', timeout=5)" 2>&1) || Status=$?
printf '%s\n' "$Denial"
[[ "$Status" != 0 ]] || { printf '%s\n' 'Unexpected worker-to-API access.' >&2; exit 1; }
grep -Ei 'timed out|TimeoutError|Connection refused' <<< "$Denial"
```

Expected failure: timeout/connection denial, not HTTP success. DNS, Kubernetes authorization and exec errors are not the expected policy result. The guarded command keeps the strict-mode session alive on the expected failure.

Test unapproved outbound HTTPS from an API pod:

```bash
pod=$(kubectl get pod -n orders -l app=order-api -o jsonpath='{.items[0].metadata.name}')
OutboundTestUtc=$(date -u +%FT%TZ)
Status=0
Denial=$(kubectl exec -n orders "$pod" -- python -c \
  "import urllib.request; urllib.request.urlopen('https://example.org', timeout=10)" 2>&1) || Status=$?
printf '%s\n' "$Denial"
[[ "$Status" != 0 ]] || { printf '%s\n' 'Unexpected unapproved outbound HTTPS access.' >&2; exit 1; }
grep -Ei 'HTTP Error (403|470)|timed out|TimeoutError|Connection (refused|reset)|RemoteDisconnected|UNEXPECTED_EOF_WHILE_READING' <<< "$Denial"
```

Expect firewall denial and a corresponding log; a generic failure without destination/rule evidence is not proof of enforcement. Confirm approved Service Bus processing still works through the gateway.

In the workspace's firewall diagnostic records, filter to the test's UTC time, destination `example.org`, and action `Deny`; retain the source IP and matched rule/default-deny evidence. The worker-to-API request uses port 80 and is denied by the application policies, while public HTTPS is permitted at the pod layer and restricted by the firewall. A TLS or DNS error without matching firewall evidence is not the expected answer.

</details>

### 6. Break and repair private DNS without changing production-like records

**Task:** simulate wrong Service Bus resolution for only the lab worker, contrast the pod and management-host resolution contexts, then recover processing. Do not alter shared private-zone records; remove the fault before proceeding or pausing.

<details>
<summary>Solution</summary>

Read the healthy record, then temporarily patch the synthetic lab worker Deployment with a pod-local host override. This deliberately interrupts that live lab worker; it is not a disposable copy. The subshell's recovery trap removes the override and waits for recovery even if diagnostics fail. Do not require a healthy rollout while the fault is active: transport failure can restart the worker.

```bash
dig "$(lab_value ServiceBusName).servicebus.windows.net"
(
  set -euo pipefail
  trap 'Status=$?; kubectl patch deployment order-worker -n orders --type merge -p "{\"spec\":{\"template\":{\"spec\":{\"hostAliases\":null}}}}" || Status=$?; kubectl rollout status deployment/order-worker -n orders --timeout=300s || Status=$?; exit "$Status"' EXIT
  patch=$(jq -nc --arg hostname "$(lab_value ServiceBusName).servicebus.windows.net" \
    '{spec:{template:{spec:{hostAliases:[{ip:"192.0.2.1",hostnames:[$hostname]}]}}}}')
  kubectl patch deployment order-worker -n orders --type merge -p "$patch"
  worker=''
  for i in {1..30}; do
    Pods=$(kubectl get pods -n orders -l app=order-worker -o json)
    worker=$(jq -r '[.items[] | select(any(.spec.hostAliases[]?; .ip == "192.0.2.1")) | .metadata.name][0] // empty' <<< "$Pods")
    if [[ -n "$worker" ]]; then break; fi
    sleep 2
  done
  [[ -n "$worker" ]] || { printf '%s\n' 'No pod with the injected host override appeared.' >&2; exit 1; }
  kubectl get pod -n orders "$worker" -o json | jq '{hostAliases:.spec.hostAliases,containers:.status.containerStatuses}'
  sleep 30
  kubectl logs -n orders "$worker" --since=5m
)
```

Inspect the injected pod's hostAliases and container state, and its Service Bus timeout/restart logs. Compare with the healthy management-host `dig` result. Kubernetes projects hostAliases into the pod's `/etc/hosts`; a CrashLoopBackOff can prevent exec diagnostics. This illustrates that resolution context matters; it is not an authoritative DNS-zone outage.

**Recovery after interruption/host loss:** the trap normally performs this automatically. If the host was lost or the subshell was forcibly terminated, run these commands before continuing:

```bash
kubectl patch deployment order-worker -n orders --type merge \
  -p '{"spec":{"template":{"spec":{"hostAliases":null}}}}'
kubectl rollout status deployment/order-worker -n orders --timeout=300s
```

Submit a new order, confirm worker processing, and check for accumulated retries/dead-letter messages.

```bash
az servicebus queue show -g "$(lab_value ResourceGroup)" --namespace-name "$(lab_value ServiceBusName)" \
  --name orders --query countDetails -o json
```

Use a new ID with the HTTPS order command in task 5 and find that same ID in the recovered worker's logs. Backlog draining is supporting evidence, not a substitute for correlating the new order.

</details>

## Exit evidence and customer discussion

Keep private resolutions, disabled public data endpoints, the effective outbound type, accepted TLS route, negative east-west/outbound evidence, firewall log correlation and restored processing.

<details>
<summary>Model answer: Does private AKS mean the app cannot be public?</summary>

No. The control-plane endpoint and application ingress are separate design choices.

</details>

<details>
<summary>Model answer: Is a NAT gateway a firewall?</summary>

NAT provides address translation and SNAT capacity, not an application destination policy.

</details>

<details>
<summary>Model answer: Why Cilium Overlay?</summary>

It conserves VNet IPs and provides the managed data plane. Direct pod-IP reachability may justify Azure CNI Pod Subnet instead.

</details>

<details>
<summary>Model answer: Why Gateway API?</summary>

It separates infrastructure and route ownership and is the modern managed ingress path. Application routing and Application Gateway for Containers have different operational/support characteristics.

</details>

<details>
<summary>Model answer: Does this give WAF, API authentication or service mTLS?</summary>

No. Gateway routing/TLS, WAF, identity-aware API controls and east-west mesh policy solve distinct problems.

</details>

## Cleanup and references

Remove the host override, keep private endpoints/firewall/policies/gateway and private ingress DNS for later labs, and protect/delete local certificate files at final teardown. At final teardown, remove only the lab-specific CA bundle after comparing the saved fingerprint with the generated certificate; do not modify system roots. Do not redeploy the lab 1 bootstrap template over these network changes. Keep `rendered/base` intact for Flux adoption and `rendered/gateway.yaml` as the separately owned platform ingress configuration.

<details>
<summary>Solution: cumulative and final certificate cleanup</summary>

For the handoff to lab 4, complete task 6's recovery and demonstrate a fresh processed order over ordinary HTTPS. Leave the private DNS zone and local CA bundle in place because later load scripts need them.

Only at **final teardown**, on the Linux host where you created the bundle, inspect the certificate and compare its exact saved fingerprint before removing the local trust files:

```bash
Fingerprint=$(< ./rendered/certs/trusted-fingerprint.txt)
ActualFingerprint=$(openssl x509 -in ./rendered/certs/tls.crt -noout -fingerprint -sha256)
if [[ ! "$Fingerprint" =~ ^[sS][hH][aA]256\ Fingerprint=([A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}$ ]] \
  || [[ "$Fingerprint" != "$ActualFingerprint" ]]; then
  printf '%s\n' 'Saved certificate fingerprint is invalid or changed; inspect renewal records before removing anything.' >&2
  exit 1
fi
openssl x509 -in ./rendered/certs/tls.crt -noout -subject -dates -fingerprint -sha256
# Run only after confirming this is the lab certificate and dependent gateways are retired.
unset CURL_CA_BUNDLE SSL_CERT_FILE REQUESTS_CA_BUNDLE
rm -- ./rendered/certs/lab-ca-bundle.pem ./rendered/certs/trusted-fingerprint.txt
```

Confirm the displayed certificate is the one generated for this lab before running the removal commands. If it was renewed, inspect the renewal record; do not remove unrelated bundles or system roots. Remove any shell startup exports for this lab bundle if you added them. Protect local keys until the dependent gateways are retired, then remove only `rendered/certs/tls.crt` and `rendered/certs/tls.key` as part of final pack teardown.

</details>

Sources reviewed 2026-09-10: [AKS firewall egress](https://learn.microsoft.com/azure/aks/limit-egress-traffic), [required outbound rules](https://learn.microsoft.com/azure/aks/outbound-rules-control-egress), [managed Gateway API](https://learn.microsoft.com/azure/aks/managed-gateway-api), [application routing Gateway API](https://learn.microsoft.com/azure/aks/app-routing-gateway-api), [Gateway TLS](https://learn.microsoft.com/azure/aks/app-routing-gateway-api-tls), [ACR Private Link](https://learn.microsoft.com/azure/container-registry/container-registry-private-link).
