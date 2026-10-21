# Lab 29 - Switch API access from public to private

**Level:** intermediate/advanced. **Type:** optional and self-contained. **Scenario:** retire a public Kubernetes API endpoint, then give an isolated operations VNet access through its own private endpoint.

**Outcome:** authenticated public access before cutover, authenticated private access before and after cutover, loss of the old public network path, and authenticated access from an unpeered VNet through Private Link with correct DNS and verified TLS.

## Lab contract

| Field | This lab |
|---|---|
| Entry | Optional disposable VNet-integrated cluster; neither cumulative deployment nor lab 28 is a prerequisite. |
| Host and identity | Fresh direct-egress workstation; Entra operator and separate read-only VM probe identities via scoped Run Command. |
| Time | Estimate: 90–150 minutes active plus 30–90 minutes cluster/VM/private-endpoint waits. |
| Cost | Two nodes, two VMs/disks, Standard AKS, two NAT/public IPs, consumer PE/DNS and traffic; no Bastion required. |
| Changes | Operator owns isolated public-to-private API cutover and unpeered consumer path; cumulative API remains untouched. |
| Exit and re-entry | Authenticated before/after paths, old public-path loss and verified TLS; reload exact tagged context and current cutover stage, never reverse exposure to retry. |

Use the [session, evidence and cost operating contract](../docs/operations.md), [stage map](../docs/state.md) and [tooling/support baseline](../docs/operations.md#tooling-and-support). Times are estimates.

## Prerequisites, cost, and safety boundary

**Use a new disposable cluster, resource group and two isolated VNets.** Do not source `use-lab.sh`, reuse cumulative settings, redeploy foundation, change the cumulative API, or connect these networks to corporate/shared networks. Labs 10 and 12 provide useful background but their deployments are not prerequisites. Lab 28 is not a prerequisite either; do not reuse its deliberately overlapping network.

Run from the repository root in a fresh Linux Bash 5+ terminal, outside any Bastion session, with Azure CLI 2.90+, `jq`, Python 3.12+, OpenSSH, `kubectl` within one minor of the selected Kubernetes version, and `kubelogin`. Use Azure public cloud and the same Entra tenant for the cluster and probe identities. The operator needs deployment, VM Run Command, user-kubeconfig retrieval and scoped role-assignment permissions, plus membership in an existing approved Entra administrator group.

Use a workstation with approved direct HTTPS egress, not an HTTP proxy, for the public before/after test. Keep its source IP and network unchanged during cutover; a newly blocked client network is not evidence that the API became private.

Approve the cost and a cleanup deadline for **two AKS nodes, AKS Standard tier, two small Ubuntu VMs/disks, two Standard NAT Gateways/public IPs, a private endpoint, private DNS and traffic**. The example requires eight D-family and four B-series vCPUs plus node-upgrade headroom. Neither VM has a public IP or allows inbound connections. No Bastion, registry, application stack or preview feature is required here.

Azure Run Command starts a bounded diagnostic on each VM using its VM agent. It does **not** proxy Kubernetes traffic: the Python client on that VM connects to the API. Each VM has its own system-assigned identity with read access only to this lab's synthetic namespace. Do not copy operator tokens or credentials to a VM or Run Command; anyone authorized to run code there can use that VM's identity. This is not `az aks command invoke` and does not give your workstation private routing.

The cluster is created with API Server VNet Integration **already enabled**. Enabling integration on an existing non-integrated cluster is a separate, permanent migration requiring an immediate stop/start and node-image rollout; it is not this exercise. Microsoft's integration guide currently labels the reverse `--disable-private-cluster` operation preview; it is deliberately excluded regardless of local CLI labeling. No live deployment or cutover has been verified in your subscription.

## 1. Compare the designs and approve the footprint

**Task:** distinguish endpoint exposure, API architecture, DNS and client connectivity. Select a supported regional patch, available VM sizes, an approved operator public egress IPv4 address and a disposable resource group.

<details>
<summary>Solution</summary>

| Design | API path and tradeoff |
|---|---|
| Public API with authorized IP ranges | Public endpoint with a source-IP allowlist; not private connectivity. Entra/RBAC still applies. |
| Private Link-based private AKS | AKS creates the API private endpoint in the cluster subnet. This is the cumulative foundation's design. Do not modify/delete that managed endpoint. |
| API Server VNet Integration | API endpoint projected into a delegated subnet behind an internal load balancer; node/API traffic is private even while public client access is enabled. This lab switches off public access. |
| VNet Integration plus Private Link Service (PLS) and consumer private endpoint (PE) | A separate VNet reaches the private API through its own PE, without peering. This lab's second stage. |

Bastion tunnels, routed peering and VPN/ExpressRoute are administrator connectivity choices, not additional API architectures. A public FQDN can resolve to a private address; **disabling a public DNS name is not equivalent to disabling public API access**. API authorized ranges protect only the public endpoint, not the private endpoint. Application ingress is independent of both.

The address plan is intentionally nonoverlapping:

| Range | Purpose |
|---|---|
| `10.90.0.0/16` | Cluster VNet: nodes `10.90.0.0/20`, delegated API subnet `10.90.16.0/27`, local probe `10.90.17.0/24`, PLS `10.90.18.0/24` |
| `10.91.0.0/16` | Unpeered operations VNet: remote probe `10.91.0.0/24`, PE `10.91.1.0/24` |
| `192.168.0.0/16` | Overlay pods |
| `172.30.0.0/16` | Kubernetes Services, DNS `172.30.0.10` |

The API subnet is delegated to `Microsoft.ContainerService/managedClusters`, contains no other workload, and is larger than the documented `/28` minimum (AKS reserves at least nine addresses). Do not enable VNet Encryption or add restrictive API-subnet NSGs without reviewing integration limitations and required ports.

```bash
set -euo pipefail
umask 077
[[ -z ${AKS_BASTION_PID:-} ]] ||
  { printf 'Use a fresh terminal outside any Bastion session.\n' >&2; exit 1; }
[[ -z ${HTTPS_PROXY:-}${https_proxy:-}${HTTP_PROXY:-}${http_proxy:-}${ALL_PROXY:-}${all_proxy:-} ]] ||
  { printf 'Use an approved direct-egress workstation for the public comparison.\n' >&2; exit 1; }
[[ $(az cloud show --query name -o tsv) == AzureCloud ]] ||
  { printf 'This guide targets Azure public cloud only.\n' >&2; exit 1; }
Work=$(mktemp -d "${TMPDIR:-/tmp}/aks-api-private-lab.XXXXXXXX")
mkdir "$Work/extensions"
export AZURE_EXTENSION_DIR="$Work/extensions"
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=no
SubscriptionId=$(az account show --query id -o tsv)
TenantId=$(az account show --query tenantId -o tsv)
Location='swedencentral'
ResourceGroup='rg-aks-api-private-lab'
ClusterName='aks-api-private-lab'
SystemVmSize='Standard_D4ds_v5'
ProbeVmSize='Standard_B2s'
Marker="lab29-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
az account show --query '{subscription:id,tenant:tenantId,name:name}' -o table
az aks get-versions --location "$Location" -o table
az vm list-usage --location "$Location" -o table
for size in "$SystemVmSize" "$ProbeVmSize"; do
  az vm list-skus --location "$Location" --size "$size" --all -o json
done
read -r -p 'Exact supported GA Kubernetes patch: ' KubernetesVersion
read -r -p 'Existing Entra administrator group object ID: ' AdminGroupObjectId
read -r -p 'Approved workstation public egress IPv4 address with /32: ' OperatorCidr
[[ "$KubernetesVersion" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
  { printf 'Supply a supported exact patch.\n' >&2; exit 1; }
[[ "$AdminGroupObjectId" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ &&
   "$AdminGroupObjectId" != 00000000-0000-0000-0000-000000000000 ]] ||
  { printf 'Supply the approved group object ID.\n' >&2; exit 1; }
python3 - "$OperatorCidr" <<'PY'
import ipaddress
import sys

network = ipaddress.IPv4Network(sys.argv[1])
if network.prefixlen != 32 or not network.network_address.is_global:
    raise SystemExit("Use exactly one approved public IPv4 /32, not a broad range.")
PY
GroupExists=$(az group exists --name "$ResourceGroup")
if [[ "$GroupExists" != false ]]; then
  printf 'Refusing to reuse an existing resource group; choose a unique disposable name.\n' >&2
  exit 1
fi
printf 'SubscriptionId=%q\nTenantId=%q\nLocation=%q\nResourceGroup=%q\nClusterName=%q\n' \
  "$SubscriptionId" "$TenantId" "$Location" "$ResourceGroup" "$ClusterName" > "$Work/context.sh"
printf 'Marker=%q\n' "$Marker" >> "$Work/context.sh"
printf 'Working folder: %s\n' "$Work"
ssh-keygen -t rsa -b 4096 -f "$Work/aks-key"
ssh-keygen -t rsa -b 4096 -f "$Work/probe-key"
```

Check current API Server VNet Integration regional availability/capacity, Azure Linux 3 support, SKU restrictions and prices. Confirm `Microsoft.ContainerService`, `Microsoft.Compute`, `Microsoft.Network` and `Microsoft.ManagedIdentity` registration; register missing providers only with the subscription owner's approval. Keep the allocation deadline and local folder path outside Git. A SKU listing is not a capacity reservation.

The fresh shell uses an empty, lab-local Azure CLI extension directory with dynamic installation disabled. This prevents a previously installed `aks-preview` from overriding core commands without removing or downgrading extensions needed by the cumulative labs. Keep these exports for this exercise only; close this fresh shell afterward. Do not write them into a shell startup file.

</details>

## 2. Create the isolated networks and probe VMs

**Task:** provide explicit outbound connectivity for nodes and VM agents, but no inbound VM access, peering, gateway connections or shared DNS links.

<details>
<summary>Solution</summary>

```bash
read -r -p "Type $ResourceGroup to approve this isolated billable experiment: " Approval
[[ "$Approval" == "$ResourceGroup" ]] ||
  { printf 'Deployment not confirmed.\n' >&2; exit 1; }
[[ $(az account show --query id -o tsv) == "$SubscriptionId" &&
   $(az account show --query tenantId -o tsv) == "$TenantId" ]] ||
  { printf 'Wrong Azure context.\n' >&2; exit 1; }
az group create -n "$ResourceGroup" -l "$Location" \
  --tags purpose=aks-api-private-lab "run=$Marker" -o none
date -u +%FT%TZ > "$Work/allocation-start.txt"
```

Define the guard used before later changes and cleanup:

```bash
assert_lab() {
  local account group
  account=$(az account show -o json) || return
  jq -e --arg subscription "$SubscriptionId" --arg tenant "$TenantId" \
    '.id == $subscription and .tenantId == $tenant' <<< "$account" >/dev/null || {
    printf 'Wrong Azure context for the API lab.\n' >&2; return 1;
  }
  group=$(az group show -n "$ResourceGroup" -o json) || return
  jq -e --arg marker "$Marker" \
    '.tags.purpose == "aks-api-private-lab" and .tags.run == $marker' <<< "$group" >/dev/null || {
    printf 'Resource group is not owned by this API-lab run.\n' >&2; return 1;
  }
}
assert_lab
```

Each VNet gets its own NAT Gateway. The API and PLS subnets do not inherit the node NAT association:

```bash
assert_lab
for side in local remote; do
  if [[ "$side" == local ]]; then
    VnetCidr='10.90.0.0/16'; ProbeCidr='10.90.17.0/24'
  else
    VnetCidr='10.91.0.0/16'; ProbeCidr='10.91.0.0/24'
  fi
  az network public-ip create -g "$ResourceGroup" -n "api-$side-nat-ip" -l "$Location" \
    --sku Standard --allocation-method Static -o none
  NatId=$(az network nat gateway create -g "$ResourceGroup" -n "api-$side-nat" -l "$Location" \
    --public-ip-addresses "api-$side-nat-ip" --query id -o tsv)
  az network vnet create -g "$ResourceGroup" -n "api-$side-vnet" -l "$Location" \
    --address-prefixes "$VnetCidr" -o none
  ProbeSubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name "api-$side-vnet" \
    -n probes --address-prefixes "$ProbeCidr" --default-outbound-access false \
    --nat-gateway "$NatId" --query id -o tsv)
  az network nsg create -g "$ResourceGroup" -n "api-$side-nsg" -l "$Location" -o none
  az network nsg rule create -g "$ResourceGroup" --nsg-name "api-$side-nsg" -n deny-inbound \
    --priority 200 --direction Inbound --access Deny --protocol '*' \
    --source-address-prefixes '*' --source-port-ranges '*' \
    --destination-address-prefixes '*' --destination-port-ranges '*' -o none
  az network nic create -g "$ResourceGroup" -n "api-$side-nic" -l "$Location" \
    --subnet "$ProbeSubnetId" --network-security-group "api-$side-nsg" -o none
  ProbeNicId=$(az network nic show -g "$ResourceGroup" -n "api-$side-nic" --query id -o tsv)
  [[ "$ProbeNicId" == */networkInterfaces/api-"$side"-nic ]] ||
    { printf 'Missing probe NIC ID; do not fall back to default VM networking.\n' >&2; exit 1; }
  az vm create -g "$ResourceGroup" -n "api-$side" -l "$Location" \
    --nics "$ProbeNicId" --image Ubuntu2404 --size "$ProbeVmSize" \
    --admin-username laboperator --ssh-key-values "$Work/probe-key.pub" --assign-identity \
    --security-type TrustedLaunch --enable-secure-boot true --enable-vtpm true \
    --storage-sku StandardSSD_LRS --os-disk-size-gb 64 -o none
done
LocalNatId=$(az network nat gateway show -g "$ResourceGroup" -n api-local-nat --query id -o tsv)
NodeSubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name api-local-vnet \
  -n nodes --address-prefixes 10.90.0.0/20 --default-outbound-access false \
  --nat-gateway "$LocalNatId" --query id -o tsv)
ApiSubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name api-local-vnet \
  -n apiserver --address-prefixes 10.90.16.0/27 \
  --delegations Microsoft.ContainerService/managedClusters --query id -o tsv)
az network vnet subnet create -g "$ResourceGroup" --vnet-name api-local-vnet \
  -n pls --address-prefixes 10.90.18.0/24 --disable-private-link-service-network-policies -o none
az network vnet subnet create -g "$ResourceGroup" --vnet-name api-remote-vnet \
  -n endpoints --address-prefixes 10.91.1.0/24 --disable-private-endpoint-network-policies true -o none
printf 'ApiSubnetId=%q\n' "$ApiSubnetId" >> "$Work/context.sh"
```

The precreated NICs have no public IP. Default outbound NSG rules allow VM-agent/identity traffic; NAT is explicit outbound connectivity, not a destination firewall. Neither VM receives an Azure subscription/resource-management role. Leave the API subnet's default intra-VNet and Azure Load Balancer communication intact.

</details>

## 3. Establish the public baseline and private preflight

**Task:** create a public, VNet-integrated API with a `/32` allowlist and local accounts disabled. Prove public operator access and private VM access to the same synthetic marker before changing exposure.

<details>
<summary>Solution</summary>

```bash
assert_lab
Identity=$(az identity create -g "$ResourceGroup" -n api-cluster -l "$Location" -o json)
ClusterIdentityId=$(jq -er .id <<< "$Identity")
ClusterPrincipalId=$(jq -er .principalId <<< "$Identity")
for subnet in "$NodeSubnetId" "$ApiSubnetId"; do
  az role assignment create --assignee-object-id "$ClusterPrincipalId" \
    --assignee-principal-type ServicePrincipal --role 'Network Contributor' --scope "$subnet" -o none
done
az aks create -g "$ResourceGroup" -n "$ClusterName" -l "$Location" \
  --kubernetes-version "$KubernetesVersion" --tier standard \
  --nodepool-name system --node-count 2 --node-vm-size "$SystemVmSize" \
  --node-osdisk-type Managed --os-sku AzureLinux3 --assign-identity "$ClusterIdentityId" \
  --vnet-subnet-id "$NodeSubnetId" --enable-apiserver-vnet-integration --apiserver-subnet-id "$ApiSubnetId" \
  --network-plugin azure --network-plugin-mode overlay --network-dataplane cilium \
  --pod-cidr 192.168.0.0/16 --service-cidr 172.30.0.0/16 --dns-service-ip 172.30.0.10 \
  --outbound-type userAssignedNATGateway --api-server-authorized-ip-ranges "$OperatorCidr" \
  --enable-aad --enable-azure-rbac --aad-admin-group-object-ids "$AdminGroupObjectId" \
  --aad-tenant-id "$TenantId" --disable-local-accounts --ssh-key-value "$Work/aks-key.pub" -o none
ClusterId=$(az aks show -g "$ResourceGroup" -n "$ClusterName" --query id -o tsv)
NodeResourceGroup=$(az aks show -g "$ResourceGroup" -n "$ClusterName" --query nodeResourceGroup -o tsv)
printf 'ClusterId=%q\nNodeResourceGroup=%q\n' "$ClusterId" "$NodeResourceGroup" >> "$Work/context.sh"
az role assignment create --assignee-object-id "$AdminGroupObjectId" --assignee-principal-type Group \
  --role 'Azure Kubernetes Service RBAC Cluster Admin' --scope "$ClusterId" -o none
az aks show -g "$ResourceGroup" -n "$ClusterName" -o json > "$Work/cluster-public.json"
jq -e --arg subnet "$ApiSubnetId" --arg cidr "$OperatorCidr" '
  .apiServerAccessProfile.enableVnetIntegration == true and
  .apiServerAccessProfile.enablePrivateCluster != true and
  .apiServerAccessProfile.subnetId == $subnet and
  .apiServerAccessProfile.authorizedIpRanges == [$cidr] and
  .disableLocalAccounts == true and .aadProfile.enableAzureRbac == true
' "$Work/cluster-public.json"
az aks get-credentials -g "$ResourceGroup" -n "$ClusterName" \
  --file "$Work/public.kubeconfig" --context "$ClusterName-lab29"
kubelogin convert-kubeconfig --kubeconfig "$Work/public.kubeconfig" -l azurecli
kubectl --kubeconfig "$Work/public.kubeconfig" --request-timeout=30s get nodes
kubectl --kubeconfig "$Work/public.kubeconfig" create namespace api-connectivity
kubectl --kubeconfig "$Work/public.kubeconfig" -n api-connectivity create configmap run-marker \
  --from-literal="run=$Marker"
kubectl --kubeconfig "$Work/public.kubeconfig" -n api-connectivity get configmap run-marker -o json |
  jq -e --arg marker "$Marker" '.data.run == $marker'
for vm in api-local api-remote; do
  PrincipalId=$(az vm show -g "$ResourceGroup" -n "$vm" --query identity.principalId -o tsv)
  az role assignment create --assignee-object-id "$PrincipalId" --assignee-principal-type ServicePrincipal \
    --role 'Azure Kubernetes Service RBAC Reader' --scope "$ClusterId/namespaces/api-connectivity" -o none
done
```

All kubeconfig operations name a new file explicitly. Do not export over, merge into or modify the cumulative/default kubeconfig. Allow RBAC propagation, then retry the failed read after diagnosis; do not grant broader privileges. A public access failure must be resolved before cutover (including a changed corporate egress IP); never replace the `/32` with `0.0.0.0/0`.

Capture the real hostname, CA and public addresses, not guessed regional DNS labels. Discover the API ILB frontend in the AKS-managed node resource group without changing it:

```bash
kubectl --kubeconfig "$Work/public.kubeconfig" config view --minify --raw -o json > "$Work/public-config.json"
PublicHost=$(python3 - "$Work/public-config.json" <<'PY'
import json
import sys
from urllib.parse import urlsplit

config = json.load(open(sys.argv[1]))
cluster = config["clusters"][0]["cluster"]
url = urlsplit(cluster["server"])
if url.scheme != "https" or not url.hostname or cluster.get("insecure-skip-tls-verify"):
    raise SystemExit("Expected a verified HTTPS kubeconfig.")
print(url.hostname)
PY
)
CaData=$(jq -er '.clusters[0].cluster["certificate-authority-data"]' "$Work/public-config.json")
python3 - "$PublicHost" > "$Work/public-ips.txt" <<'PY'
import ipaddress
import socket
import sys

addresses = sorted({item[4][0] for item in socket.getaddrinfo(sys.argv[1], 443, socket.AF_INET)})
if not addresses or not all(ipaddress.IPv4Address(address).is_global for address in addresses):
    raise SystemExit("Expected public IPv4 resolution before cutover.")
for address in addresses:
    with socket.create_connection((address, 443), timeout=10):
        pass
print("\n".join(addresses))
PY
printf 'PublicHost=%q\nCaData=%q\n' "$PublicHost" "$CaData" >> "$Work/context.sh"
discover_frontend() {
  local lb frontend
  lb=$(az network lb show -g "$NodeResourceGroup" -n kube-apiserver -o json) || return
  frontend=$(jq -ce --arg subnet "$ApiSubnetId" '
    [.frontendIPConfigurations[] | select(.subnet.id == $subnet and .privateIPAddress != null)]
    | if length == 1 then .[0] else error("Expected exactly one API subnet frontend") end
  ' <<< "$lb") || return
  FrontendId=$(jq -er .id <<< "$frontend") || return
  ApiIp=$(jq -er .privateIPAddress <<< "$frontend") || return
  python3 - "$ApiIp" <<'PY'
import ipaddress
import sys

if ipaddress.IPv4Address(sys.argv[1]) not in ipaddress.IPv4Network("10.90.16.0/27"):
    raise SystemExit("API frontend is outside the isolated delegated subnet.")
PY
}
discover_frontend
```

Create one small diagnostic client used on both VMs and for the external negative test. `direct` explicitly selects an IP but still verifies the original hostname and cluster CA. `dns` additionally requires normal VM DNS to resolve **only** to the expected IP. `unreachable` accepts only a classified TCP connectivity failure, not a DNS/TLS/authentication error.

```bash
cat > "$Work/probe.py" <<'PY'
import base64
import errno
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.request

config = json.loads(base64.b64decode(sys.argv[1], validate=True))
host, address, mode, marker = (config[key] for key in ("host", "ip", "mode", "marker"))
ipaddress.IPv4Address(address)
if mode not in ("direct", "dns", "unreachable") or not re.fullmatch(r"lab29-[0-9a-f]{16}", marker):
    raise SystemExit("Invalid probe mode or marker.")
if not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
    raise SystemExit("Invalid API hostname.")
if mode == "unreachable":
    try:
        connection = socket.create_connection((address, 443), timeout=5)
    except OSError as error:
        if not isinstance(error, TimeoutError) and error.errno not in (
            errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT
        ):
            raise
        print(f"API_UNREACHABLE:{marker}")
    else:
        connection.close()
        raise SystemExit("Unexpected TCP connectivity; the negative test has failed.")
else:
    if mode == "dns":
        resolved = {item[4][0] for item in socket.getaddrinfo(host, 443, socket.AF_INET)}
        if resolved != {address}:
            raise SystemExit(f"Wrong DNS: {sorted(resolved)}, expected only {address}")
        print(f"DNS_OK:{host}:{address}")
    ca = base64.b64decode(config["ca"], validate=True).decode()
    context = ssl.create_default_context(cadata=ca)
    client = http.client.HTTPSConnection(host, timeout=10, context=context)
    with socket.create_connection((address, 443), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secure:
            client.sock = secure
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                "http://169.254.169.254/metadata/identity/oauth2/token"
                "?api-version=2018-02-01&resource=6dae42f8-4368-4678-94ff-3960e28e3630",
                headers={"Metadata": "true"},
            )
            with opener.open(request, timeout=10) as response:
                token = json.load(response)["access_token"]
            client.request(
                "GET", "/api/v1/namespaces/api-connectivity/configmaps/run-marker",
                headers={"Authorization": f"Bearer {token}"},
            )
            response = client.getresponse()
            if response.status != 200:
                raise SystemExit(f"API HTTP {response.status}; diagnose identity/RBAC, not just networking.")
            result = json.load(response)
            if result.get("kind") != "ConfigMap" or result.get("data", {}).get("run") != marker:
                raise SystemExit("Wrong cluster or synthetic marker.")
    print(f"API_OK:{marker}")
PY
make_probe() {
  local mode=$1 address=$2 host=$3 payload
  payload=$(jq -nc --arg mode "$mode" --arg ip "$address" --arg host "$host" \
    --arg marker "$Marker" --arg ca "$CaData" \
    '{mode:$mode,ip:$ip,host:$host,marker:$marker,ca:$ca}' | base64 -w0) || return
  {
    printf "python3 - '%s' <<'API_PROBE'\n" "$payload"
    cat "$Work/probe.py" || return
    printf '\nAPI_PROBE\n'
  } > "$Work/current-probe.sh"
}
probe_vm() {
  local vm=$1 mode=$2 address=$3 host=$4 result expected
  assert_lab || return
  [[ "$vm" == api-local || "$vm" == api-remote ]] ||
    { printf 'Unknown probe VM.\n' >&2; return 1; }
  make_probe "$mode" "$address" "$host" || return
  result=$(az vm run-command invoke -g "$ResourceGroup" -n "$vm" \
    --command-id RunShellScript --scripts @"$Work/current-probe.sh" -o json) || return
  jq -er '.value[].message' <<< "$result" | tee "$Work/probe-last.txt" || return
  expected="API_OK:$Marker"
  if [[ "$mode" == unreachable ]]; then expected="API_UNREACHABLE:$Marker"; fi
  grep -Fx "$expected" "$Work/probe-last.txt"
}
probe_vm api-local direct "$ApiIp" "$PublicHost"
cp "$Work/probe-last.txt" "$Work/private-preflight.txt"
```

Require `API_OK:<run marker>` before continuing. The token is obtained locally from IMDS for the AKS server audience, held only in process memory, and never printed or placed in Run Command input. The request uses the cluster CA and original hostname, even though it selects the private ILB IP. Python is supplied by Ubuntu 24.04; no tool download or operator login on either VM is needed.

The public operator request and local VM request read the same marker with different Entra principals. This proves the private route, TLS and namespace-scoped authorization independently of the public endpoint. It does not yet prove private DNS; task 4 does.

</details>

## 4. Disable public API access and prove the cutover

**Task:** enable private-cluster mode on this already-integrated cluster, verify DNS/private access, and prove the previously reachable public addresses no longer accept TCP 443.

<details>
<summary>Solution</summary>

Keep the workstation's approved egress/network unchanged for the before/after comparison. Obtain fresh user configuration into a **different** file; credential retrieval uses ARM and does not require Kubernetes API connectivity.

```bash
assert_lab
read -r -p "Type $ClusterName to disable ONLY this cluster's public API: " Approval
[[ "$Approval" == "$ClusterName" ]] ||
  { printf 'Cutover not confirmed.\n' >&2; exit 1; }
az aks update -g "$ResourceGroup" -n "$ClusterName" --enable-private-cluster -o none
az aks show -g "$ResourceGroup" -n "$ClusterName" -o json > "$Work/cluster-private.json"
jq -e --arg id "$ClusterId" '.id == $id and
       .apiServerAccessProfile.enableVnetIntegration == true and
       .apiServerAccessProfile.enablePrivateCluster == true and .disableLocalAccounts == true' \
  "$Work/cluster-private.json"
az aks get-credentials -g "$ResourceGroup" -n "$ClusterName" \
  --file "$Work/private.kubeconfig" --context "$ClusterName-lab29-private"
kubectl --kubeconfig "$Work/private.kubeconfig" config view --minify --raw -o json > "$Work/private-config.json"
ApiHost=$(python3 - "$Work/private-config.json" <<'PY'
import json
import sys
from urllib.parse import urlsplit

cluster = json.load(open(sys.argv[1]))["clusters"][0]["cluster"]
url = urlsplit(cluster["server"])
if url.scheme != "https" or not url.hostname or cluster.get("insecure-skip-tls-verify"):
    raise SystemExit("Expected a verified HTTPS kubeconfig.")
print(url.hostname)
PY
)
CaData=$(jq -er '.clusters[0].cluster["certificate-authority-data"]' "$Work/private-config.json")
discover_frontend
probe_vm api-local dns "$ApiIp" "$ApiHost"
cp "$Work/probe-last.txt" "$Work/private-after.txt"
while IFS= read -r PublicIp; do
  make_probe unreachable "$PublicIp" "$PublicHost"
  bash "$Work/current-probe.sh" | tee "$Work/public-denied-$PublicIp.txt"
done < "$Work/public-ips.txt"
probe_vm api-remote unreachable "$ApiIp" "$ApiHost"
cp "$Work/probe-last.txt" "$Work/unpeered-before.txt"
printf 'ApiHost=%q\nCaData=%q\n' "$ApiHost" "$CaData" >> "$Work/context.sh"
```

Expect `DNS_OK` and `API_OK` on the local VM, `API_UNREACHABLE` from the workstation for every recorded old public address, and `API_UNREACHABLE` from the unpeered VM to the ILB IP. A successful ARM update or failed workstation DNS lookup alone is not sufficient evidence. A TCP connection followed by HTTP 401/403 is still connectivity and fails the public-network negative test.

AKS manages the cluster-side private DNS configuration during conversion. If DNS has not converged, inspect the private zone, VNet link and actual kubeconfig hostname; retry only after diagnosis. Do not infer success from a direct-IP test or disable TLS verification. Retained authorized ranges do not govern the now-private endpoint.

If private DNS fails, the `direct` probe with the current ILB IP/hostname can separate DNS from routing/TLS/identity. ARM and VM Run Command remain available for diagnosis and teardown even without API access. **Do not reopen the public API as an automatic rollback.** Repair only this lab's DNS/permissions or tear down the disposable group.

</details>

## 5. Add a private endpoint in the unpeered operations VNet

**Task:** expose the integrated API through a narrowly visible PLS, connect a PE in the remote VNet, and map the exact API hostname to the PE address only for that VNet.

<details>
<summary>Solution</summary>

This is the documented PLS path for **BYO-VNet API Server VNet Integration**, not the classic managed AKS private-endpoint design. Reference the discovered `kube-apiserver` frontend; do not edit its load-balancer rules or backend pools.

```bash
assert_lab
discover_frontend
az network private-link-service create -g "$ResourceGroup" -n api-pls -l "$Location" \
  --vnet-name api-local-vnet --subnet pls --lb-frontend-ip-configs "$FrontendId" \
  --visibility "$SubscriptionId" --auto-approval "$SubscriptionId" --enable-proxy-protocol false -o none
PlsId=$(az network private-link-service show -g "$ResourceGroup" -n api-pls --query id -o tsv)
az network private-endpoint create -g "$ResourceGroup" -n api-pe -l "$Location" \
  --vnet-name api-remote-vnet --subnet endpoints \
  --private-connection-resource-id "$PlsId" --connection-name api-connection -o none
az network private-endpoint show -g "$ResourceGroup" -n api-pe -o json > "$Work/private-endpoint.json"
jq -e --arg pls "$PlsId" '
  .provisioningState == "Succeeded" and
  (.privateLinkServiceConnections | length) == 1 and
  .privateLinkServiceConnections[0].privateLinkServiceId == $pls and
  .privateLinkServiceConnections[0].privateLinkServiceConnectionState.status == "Approved"
' "$Work/private-endpoint.json"
PeNicId=$(jq -er '.networkInterfaces[0].id' "$Work/private-endpoint.json")
PeIp=$(az network nic show --ids "$PeNicId" --query 'ipConfigurations[0].privateIPAddress' -o tsv)
python3 - "$PeIp" <<'PY'
import ipaddress
import sys

if ipaddress.IPv4Address(sys.argv[1]) not in ipaddress.IPv4Network("10.91.1.0/24"):
    raise SystemExit("PE address is outside the isolated endpoint subnet.")
PY
RemoteVnetId=$(az network vnet show -g "$ResourceGroup" -n api-remote-vnet --query id -o tsv)
az network private-dns zone create -g "$ResourceGroup" -n "$ApiHost" -o none
az network private-dns record-set a add-record -g "$ResourceGroup" --zone-name "$ApiHost" \
  --record-set-name '@' --ipv4-address "$PeIp" -o none
az network private-dns link vnet create -g "$ResourceGroup" --zone-name "$ApiHost" \
  -n remote-api --virtual-network "$RemoteVnetId" --registration-enabled false -o none
for vnet in api-local-vnet api-remote-vnet; do
  az network vnet show -g "$ResourceGroup" -n "$vnet" -o json |
    jq -e '(.virtualNetworkPeerings | length) == 0'
done
probe_vm api-remote dns "$PeIp" "$ApiHost"
cp "$Work/probe-last.txt" "$Work/private-endpoint-after.txt"
probe_vm api-remote unreachable "$ApiIp" "$ApiHost"
probe_vm api-local dns "$ApiIp" "$ApiHost"
```

The private zone uses the **entire actual API hostname**, with an apex `@` record, so it overrides only that hostname inside the remote VNet. It deliberately does not link or change AKS's managed zone: the local VM must still resolve to the ILB, while the remote VM resolves to its PE. Do not guess `private.<region>.azmk8s.io`, copy a DNS label from another cluster or shadow a broad shared parent zone.

Require remote `DNS_OK:<API hostname>:<PE IP>` and `API_OK:<run marker>`, continued denial to the original ILB IP from that same VM, and continued local success. The request path is:

```text
remote probe identity -> remote PE -> PLS -> API ILB -> Entra/RBAC -> synthetic ConfigMap
```

PLS visibility/automatic approval is limited to this subscription, never `*`. For cross-subscription production consumers, explicitly approve the consumer connection and design DNS ownership; that extension is outside this bounded lab. PLS must not inject PROXY protocol into the API TLS stream.

</details>

## Exit evidence and customer discussion

Retain the public/private API profiles, unchanged cluster resource ID, public operator marker read, local private preflight, private DNS/TLS/authenticated reads after cutover, denial on all saved public IPs, approved PE/PLS connection, absence of peering, remote DNS-to-PE evidence, and continued denial to the ILB from the remote VM. Keep tokens, private keys and kubeconfigs out of Git.

<details>
<summary>Model answer: Did we move the whole control plane into our subscription?</summary>

No. AKS still operates the managed control plane. VNet Integration projects the API endpoint into a delegated subnet; it is not self-managed control-plane VMs or an application ingress configuration.

</details>

<details>
<summary>Model answer: Why is adding another private endpoint not a required foundation fix?</summary>

The cumulative foundation already uses AKS-managed Private Link for its API. This lab explores a different architecture and an additional consumer-network access pattern. Neither makes ACR, databases or applications private automatically.

</details>

<details>
<summary>Model answer: Does an approved private endpoint authorize Kubernetes operations?</summary>

No. It supplies network connectivity. TLS verifies the server, Entra authenticates each probe identity, and namespace-scoped Azure RBAC authorizes the read. The private API does not inherit the public authorized-IP allowlist.

</details>

## Cleanup and interrupted-run recovery

**Task:** remove only this experiment, including both NAT Gateways, VMs/identities, PE/PLS and DNS. Cleanup is required even after a failed deployment or cutover; no Kubernetes request is needed.

<details>
<summary>Solution</summary>

If the terminal was lost, set `Work` to the exact printed folder, inspect and source **your own** `"$Work/context.sh"`, and redefine `assert_lab` from task 2. The saved context is for identification/cleanup, not a resumable deployment script. Do not replay creation over partial resources; inspect the failed stage, recover its required variables from Azure, or tear down and start a new run.

From the original workstation, save intended evidence, confirm the group ownership and inventory, then explicitly approve deletion:

```bash
assert_lab
az resource list -g "$ResourceGroup" --query '[].{name:name,type:type}' -o table
read -r -p "Type $ResourceGroup to permanently delete ONLY this API experiment: " Approval
[[ "$Approval" == "$ResourceGroup" ]] ||
  { printf 'Cleanup not confirmed.\n' >&2; exit 1; }
az group delete -n "$ResourceGroup" --yes
GroupExists=$(az group exists --name "$ResourceGroup")
[[ "$GroupExists" == false ]] ||
  { printf 'Resource group deletion is incomplete; inspect remaining resources.\n' >&2; exit 1; }
if [[ -n ${NodeResourceGroup:-} ]]; then
  NodeGroupExists=$(az group exists --name "$NodeResourceGroup")
  [[ "$NodeGroupExists" == false ]] ||
    { printf 'AKS-managed node group remains; inspect AKS cleanup before proceeding.\n' >&2; exit 1; }
fi
```

Record the AKS-managed node resource group even if cluster provisioning failed; inspect deployment/resource state if task 3 did not reach discovery. AKS owns that group's removal. If deletion reports a PLS/frontend dependency, remove this group's `api-pe`, then `api-pls`, and retry the confirmed teardown; do not manually delete unrelated managed load-balancer components.

Check retained disks, public IPs, role assignments and Cost Management afterward. Deallocating VMs or closing the shell does not stop NAT/PE/DNS/cluster charges. Remove only the exact local kubeconfigs, probe inputs, keys and evidence files no longer needed from the printed working folder; never recursively delete the repository or another session's folder. Your default/cumulative kubeconfig was never changed.

</details>

## References and support

Sources reviewed **2026-10-20**: [Private Link-based AKS clusters](https://learn.microsoft.com/azure/aks/private-clusters), [API Server VNet Integration and public/private transitions](https://learn.microsoft.com/azure/aks/api-server-vnet-integration), [Private Link for an integrated API in another VNet](https://learn.microsoft.com/azure/aks/private-apiserver-vnet-integration-cluster), [API authorized ranges](https://learn.microsoft.com/azure/aks/api-server-authorized-ip-ranges), [Azure RBAC for Kubernetes](https://learn.microsoft.com/azure/aks/manage-azure-rbac), [AKS token audience](https://azure.github.io/kubelogin/concepts/aks.html), [Linux Run Command](https://learn.microsoft.com/azure/virtual-machines/linux/run-command).

Check current regional capacity and CLI support before spending. Unlike the cumulative Bastion workflow, this exercise does not require `aks-preview`; do not enable the preview private-to-public reversal. Offline syntax and mocked probe checks are not live Azure validation.

---

**Course complete:** [Return to the lab index](../README.md).
