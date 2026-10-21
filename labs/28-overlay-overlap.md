# Lab 28 - Break pod connectivity with an overlapping peered network

**Level:** advanced. **Type:** optional and self-contained. **Scenario:** peering says Connected and a VM can reach the application, but an AKS pod cannot. Demonstrate why overlay address planning still matters when outbound traffic uses source NAT.

**Outcome:** an actual HTTP service in a peered VNet, a successful independent VM request, a failed pod request to the same IP, and successful requests from both clients after removing the overlap.

## Lab contract

| Field | This lab |
|---|---|
| Entry | Optional isolated experiment; never connect its deliberately overlapping VNets to cumulative/shared networks. |
| Host and identity | Fresh workstation session; Entra platform operator, scoped Run Command and role authority; dedicated native Bastion preview path. |
| Time | Estimate: 90–150 minutes active plus 30–90 minutes cluster/VM/network waits. |
| Cost | Two AKS nodes, two VMs/disks, Standard AKS, NAT/public IP, Bastion/public IP and peering; approve a cleanup deadline. |
| Changes | Operator owns only tagged disposable resources; pod/server collision is intentional unsupported design, not recommended networking. |
| Exit and re-entry | Same-IP VM-success/pod-failure and nonoverlapping recovery evidence; reload run marker/context and inspect exact allocations before cleanup. |

Use the [session, evidence and cost operating contract](../docs/operations.md), [stage map](../docs/state.md) and [tooling/support baseline](../docs/operations.md#tooling-and-support). Times are estimates.

## Prerequisites, cost, and safety boundary

**This deliberately creates an unsupported network design. Use a new disposable resource group and isolated VNets only.** Never peer this experiment with the cumulative foundation, corporate networks, VPN/ExpressRoute, shared DNS or another cluster. Do not change the existing labs' pod CIDR or infrastructure. The ordinary rule remains: pod, Service and connected external address spaces must not overlap.

Use Linux Bash 5+ or Zsh 5.9+, Python 3.12+, Azure CLI 2.90+, Bicep, `jq`, OpenSSH, `kubectl` within one minor of the selected API version, and `kubelogin`. Follow the [shell setup](../docs/operations.md#bash-and-zsh) and run from the repository root in a **fresh terminal outside any existing Bastion session**. The operator needs resource deployment, Azure Run Command and scoped role-assignment authority, plus membership in an existing Entra administrator group.

Budget for two AKS nodes, two small Ubuntu VMs with managed disks, AKS Standard tier, one Standard NAT Gateway/public IP, Bastion Standard with two scale units/public IP, and peering traffic. Default compute is eight D-family node vCPUs plus four B-series VM vCPUs; check regional/family quota and surge headroom. Approve an allocation deadline and cleanup owner before starting. Closing a terminal or deallocating one VM does not stop the other charges.

The experiment uses a private Entra/Azure-RBAC API with local accounts disabled, and the same explicitly accepted **native Bastion `aks-preview` exception** as the cumulative labs. Neither VM has a public IP, SSH ingress or managed-identity privileges. A private control VM in the **node VNet** runs requests through Azure Run Command; this is not `az aks command invoke` or a host-network pod. Only node/control subnets may reach the service on TCP 8080.

The pod is the only application workload in this dedicated two-node system pool; this small diagnostic footprint is not a production pool-isolation recommendation. Use only synthetic data. No live deployment or fault execution has been verified in your subscription.

## 1. Predict the failure and approve the isolated footprint

**Task:** explain why peering and source NAT cannot resolve an ambiguous destination. Select a supported regional version, VM sizes and unique names before creating anything.

<details>
<summary>Solution</summary>

| Address space | Purpose |
|---|---|
| `10.80.0.0/16` | Node VNet: nodes `/20`, control VM `10.80.16.0/24`, Bastion `10.80.19.0/26` |
| `192.168.0.0/16` | AKS overlay pod CIDR; intentionally conflicts with the peer below |
| `172.29.0.0/16` | Kubernetes Services; DNS `172.29.0.10` |
| Actual diagnostic pod's `/24` | Initial service subnet in the peer VNet; service VM gets that pod's exact IP |
| `10.81.0.0/24` | Nonoverlapping recovery subnet in the same peer VNet; server moves to `10.81.0.10` |

The two **VNet** address spaces do not overlap. The conflict is between the cluster's **overlay pod CIDR** and a peered VNet, so Connected peering does not prove pod reachability.

An exact pod/server IP collision makes this a deterministic worst case: the pod's destination is also its own local address. The pod runs no HTTP server, so the remote service cannot be reached at that address. Other partially overlapping destinations can fail differently depending on allocated node blocks and the data plane; do not claim every overlap always causes the same timeout.

Create an isolated local working folder and select values. Change the example region/names as needed, but keep the demonstrated CIDRs unless you consistently update every dependent command and verify the new address plan.

```bash
set -euo pipefail
umask 077
[[ -z ${AKS_BASTION_PID:-} ]] || {
  printf 'Use a fresh terminal outside any active Bastion session.\n' >&2; exit 1;
}
Work=$(mktemp -d "${TMPDIR:-/tmp}/aks-overlay-overlap-lab.XXXXXXXX")
SubscriptionId=$(az account show --query id -o tsv)
TenantId=$(az account show --query tenantId -o tsv)
Location='swedencentral'
ResourceGroup='rg-aks-overlay-overlap-lab'
ClusterName='aks-overlay-overlap-lab'
SystemVmSize='Standard_D4ds_v5'
TestVmSize='Standard_B2s'
PodCidr='192.168.0.0/16'
NodeCidr='10.80.0.0/16'
ServiceCidr='172.29.0.0/16'
RecoveryCidr='10.81.0.0/24'
RecoveryIp='10.81.0.10'
Marker="lab28-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
az account show --query '{subscription:id,tenant:tenantId,name:name}' -o table
az aks get-versions --location "$Location" -o table
az vm list-usage --location "$Location" -o table
for size in "$SystemVmSize" "$TestVmSize"; do
  az vm list-skus --location "$Location" --size "$size" --all -o json
done
printf '%s' 'Exact supported GA Kubernetes patch: ' >&2
read -r KubernetesVersion
printf '%s' 'Existing Entra administrator group object ID: ' >&2
read -r AdminGroupObjectId
[[ "$KubernetesVersion" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
  { printf 'Supply a supported exact patch.\n' >&2; exit 1; }
[[ "$AdminGroupObjectId" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ &&
   "$AdminGroupObjectId" != 00000000-0000-0000-0000-000000000000 ]] ||
  { printf 'Supply the approved group object ID.\n' >&2; exit 1; }
GroupExists=$(az group exists --name "$ResourceGroup")
if [[ "$GroupExists" != false ]]; then
  printf 'Refusing to reuse an existing resource group; choose a unique disposable name.\n' >&2
  exit 1
fi
printf 'SubscriptionId=%q\nTenantId=%q\nLocation=%q\nResourceGroup=%q\nClusterName=%q\n' \
  "$SubscriptionId" "$TenantId" "$Location" "$ResourceGroup" "$ClusterName" > "$Work/context.sh"
printf 'SystemVmSize=%q\nTestVmSize=%q\nKubernetesVersion=%q\nAdminGroupObjectId=%q\n' \
  "$SystemVmSize" "$TestVmSize" "$KubernetesVersion" "$AdminGroupObjectId" >> "$Work/context.sh"
printf 'PodCidr=%q\nNodeCidr=%q\nServiceCidr=%q\nRecoveryCidr=%q\nRecoveryIp=%q\nMarker=%q\n' \
  "$PodCidr" "$NodeCidr" "$ServiceCidr" "$RecoveryCidr" "$RecoveryIp" "$Marker" >> "$Work/context.sh"
printf 'Working folder: %s\n' "$Work"
ssh-keygen -t rsa -b 4096 -f "$Work/aks-key"
ssh-keygen -t rsa -b 4096 -f "$Work/vm-key"
```

Review SKU restrictions, supported Azure Linux 3 versions and the actual prices. Confirm `Microsoft.ContainerService`, `Microsoft.Compute`, `Microsoft.Network` and `Microsoft.ManagedIdentity` are registered; register only missing providers with the subscription owner's approval. Record the allocation deadline outside Git. Do not run with an unreviewed subscription context or unresolved quota.

</details>

## 2. Create private management and independent control paths

**Task:** provision the node VNet, explicit egress, Bastion and an independent control VM. Keep all resources inside the new boundary; do not use the cumulative `local.settings.json`.

<details>
<summary>Solution</summary>

Approve the full billable inventory before the first create:

```bash
printf '%s' "Type $ResourceGroup to approve this isolated billable experiment: " >&2
read -r Approval
[[ "$Approval" == "$ResourceGroup" ]] ||
  { printf 'Deployment not confirmed.\n' >&2; exit 1; }
[[ $(az account show --query id -o tsv) == "$SubscriptionId" ]] ||
  { printf 'Wrong subscription; stop and restore the approved context.\n' >&2; exit 1; }
az group create -n "$ResourceGroup" -l "$Location" \
  --tags purpose=aks-overlay-overlap-lab "run=$Marker" -o none
date -u +%FT%TZ > "$Work/allocation-start.txt"
```

Define this guard for subsequent stages and recovery. It checks the saved tenant/subscription and this run's group tags, without switching context:

```bash
assert_lab() {
  local account group
  account=$(az account show -o json) || return
  jq -e --arg subscription "$SubscriptionId" --arg tenant "$TenantId" \
    '.id == $subscription and .tenantId == $tenant' <<< "$account" >/dev/null || {
    printf 'Wrong Azure context for the overlap lab.\n' >&2; return 1;
  }
  group=$(az group show -n "$ResourceGroup" -o json) || return
  jq -e --arg marker "$Marker" \
    '.tags.purpose == "aks-overlay-overlap-lab" and .tags.run == $marker' <<< "$group" >/dev/null || {
    printf 'Resource group is not owned by this overlap-lab run.\n' >&2; return 1;
  }
}
assert_lab
```

Create explicit node/control egress. NAT Gateway belongs only to these two subnets, **never** the Bastion subnet:

```bash
az network public-ip create -g "$ResourceGroup" -n overlap-nat-ip -l "$Location" \
  --sku Standard --allocation-method Static -o none
NatId=$(az network nat gateway create -g "$ResourceGroup" -n overlap-nat -l "$Location" \
  --public-ip-addresses overlap-nat-ip --query id -o tsv)
az network vnet create -g "$ResourceGroup" -n overlap-nodes -l "$Location" \
  --address-prefixes "$NodeCidr" -o none
NodeSubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name overlap-nodes \
  -n nodes --address-prefixes 10.80.0.0/20 --default-outbound-access false \
  --nat-gateway "$NatId" --query id -o tsv)
ControlSubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name overlap-nodes \
  -n control --address-prefixes 10.80.16.0/24 --default-outbound-access false \
  --nat-gateway "$NatId" --query id -o tsv)
az network vnet subnet create -g "$ResourceGroup" --vnet-name overlap-nodes \
  -n AzureBastionSubnet --address-prefixes 10.80.19.0/26 -o none
NodeVnetId=$(az network vnet show -g "$ResourceGroup" -n overlap-nodes --query id -o tsv)
az deployment group what-if -g "$ResourceGroup" -n bastion -f ./infra/bastion.bicep \
  -p prefix=ovl28 "location=$Location" vnetName=overlap-nodes
# Inspect the reviewed module's two scale units/public IP before applying.
az deployment group create -g "$ResourceGroup" -n bastion -f ./infra/bastion.bicep \
  -p prefix=ovl28 "location=$Location" vnetName=overlap-nodes -o none
BastionId=$(az deployment group show -g "$ResourceGroup" -n bastion \
  --query properties.outputs.bastionId.value -o tsv)
az network nsg create -g "$ResourceGroup" -n overlap-control-nsg -l "$Location" -o none
az network nsg rule create -g "$ResourceGroup" --nsg-name overlap-control-nsg -n deny-inbound \
  --priority 200 --direction Inbound --access Deny --protocol '*' \
  --source-address-prefixes '*' --source-port-ranges '*' \
  --destination-address-prefixes '*' --destination-port-ranges '*' -o none
az network nic create -g "$ResourceGroup" -n overlap-control-nic -l "$Location" \
  --subnet "$ControlSubnetId" --network-security-group overlap-control-nsg \
  --private-ip-address 10.80.16.10 -o none
ControlNicId=$(az network nic show -g "$ResourceGroup" -n overlap-control-nic --query id -o tsv)
[[ "$ControlNicId" == */networkInterfaces/overlap-control-nic ]] ||
  { printf 'Missing control NIC identity; do not create a VM with default networking.\n' >&2; exit 1; }
az vm create -g "$ResourceGroup" -n overlap-control -l "$Location" \
  --nics "$ControlNicId" --image Ubuntu2404 --size "$TestVmSize" \
  --admin-username laboperator --ssh-key-values "$Work/vm-key.pub" \
  --security-type TrustedLaunch --enable-secure-boot true --enable-vtpm true \
  --storage-sku StandardSSD_LRS --os-disk-size-gb 64 -o none
printf 'NodeSubnetId=%q\nNodeVnetId=%q\nBastionId=%q\n' \
  "$NodeSubnetId" "$NodeVnetId" "$BastionId" >> "$Work/context.sh"
```

The precreated NIC has no public IP. The control VM allows no inbound connections; Azure Run Command uses its VM agent and explicit outbound path. An ARM success is not proof the VM agent or Python is ready: the marker-checked HTTP request in task 5 is the acceptance test.

</details>

## 3. Start the private overlay cluster and one diagnostic pod

**Task:** create the new cluster with Cilium and explicit CIDRs, use the existing native Bastion helper with an explicit target, and record the diagnostic pod's identity/address.

<details>
<summary>Solution</summary>

```bash
assert_lab
Identity=$(az identity create -g "$ResourceGroup" -n overlap-cluster -l "$Location" -o json)
ClusterIdentityId=$(jq -er .id <<< "$Identity")
ClusterPrincipalId=$(jq -er .principalId <<< "$Identity")
az role assignment create --assignee-object-id "$ClusterPrincipalId" --assignee-principal-type ServicePrincipal \
  --role 'Network Contributor' --scope "$NodeVnetId" -o none
az aks create -g "$ResourceGroup" -n "$ClusterName" -l "$Location" \
  --kubernetes-version "$KubernetesVersion" --tier standard \
  --nodepool-name system --node-count 2 --node-vm-size "$SystemVmSize" \
  --node-osdisk-type Managed --os-sku AzureLinux3 \
  --assign-identity "$ClusterIdentityId" --vnet-subnet-id "$NodeSubnetId" \
  --network-plugin azure --network-plugin-mode overlay --network-dataplane cilium \
  --pod-cidr "$PodCidr" --service-cidr "$ServiceCidr" --dns-service-ip 172.29.0.10 \
  --outbound-type userAssignedNATGateway --enable-private-cluster --disable-public-fqdn \
  --enable-aad --enable-azure-rbac --aad-admin-group-object-ids "$AdminGroupObjectId" \
  --disable-local-accounts --ssh-key-value "$Work/aks-key.pub" -o none
ClusterId=$(az aks show -g "$ResourceGroup" -n "$ClusterName" --query id -o tsv)
NodeResourceGroup=$(az aks show -g "$ResourceGroup" -n "$ClusterName" --query nodeResourceGroup -o tsv)
printf 'NodeResourceGroup=%q\n' "$NodeResourceGroup" >> "$Work/context.sh"
az role assignment create --assignee-object-id "$AdminGroupObjectId" --assignee-principal-type Group \
  --role 'Azure Kubernetes Service RBAC Cluster Admin' --scope "$ClusterId" -o none
az extension add --name aks-preview --version 22.0.0b5 --upgrade
az extension add --name bastion --version 1.4.3 --upgrade
source ./scripts/activate-aks-bastion.sh --resource-group "$ResourceGroup" \
  --cluster-name "$ClusterName" --bastion-id "$BastionId" --context "$ClusterName-lab28" --port 8445
kubectl --request-timeout=30s get nodes
```

Keep this owning shell open. The helper checks private API/Entra/Bastion configuration and supplies a mode-600 temporary kubeconfig with the original CA/hostname. Never fetch credentials over it. Confirm the active context is this lab's, and record the actual network profile:

```bash
assert_cluster() {
  assert_lab || return
  [[ $(kubectl config current-context) == "$ClusterName-lab28" ]] || {
    printf 'Wrong Kubernetes context for the overlap lab.\n' >&2; return 1;
  }
}
assert_cluster
az aks show -g "$ResourceGroup" -n "$ClusterName" --query networkProfile -o json |
  tee "$Work/network-profile.json"
```

Deploy one small, non-root client that runs Python but **does not listen on port 8080**. The official Python tag is a discovery input for this disposable lab; record the pulled digest, and use an approved mirrored digest in restricted environments.

```bash
cat > "$Work/client.yaml" <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: overlay-overlap
  labels:
    pod-security.kubernetes.io/enforce: restricted
---
apiVersion: v1
kind: Pod
metadata:
  name: overlap-client
  namespace: overlay-overlap
spec:
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: client
    image: docker.io/library/python:3.12-slim
    command: ["python", "-c", "import time; time.sleep(86400)"]
    resources:
      requests: {cpu: 25m, memory: 32Mi}
      limits: {cpu: 100m, memory: 64Mi}
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: ["ALL"]
YAML
kubectl apply -f "$Work/client.yaml"
kubectl wait -n overlay-overlap pod/overlap-client --for=condition=Ready --timeout=300s
kubectl get pod -n overlay-overlap overlap-client -o json > "$Work/client-before.json"
PodIp=$(jq -er .status.podIP "$Work/client-before.json")
PodUid=$(jq -er .metadata.uid "$Work/client-before.json")
jq '{uid:.metadata.uid,ip:.status.podIP,node:.spec.nodeName,images:[.status.containerStatuses[].imageID]}' \
  "$Work/client-before.json"
PeerCidr=$(python3 - "$PodIp" "$PodCidr" "$NodeCidr" "$RecoveryCidr" "$ServiceCidr" <<'PY'
import ipaddress
import sys

address = ipaddress.IPv4Address(sys.argv[1])
pod, nodes, recovery, services = map(ipaddress.IPv4Network, sys.argv[2:])
peer = ipaddress.IPv4Network(f"{address}/24", strict=False)
if address not in pod or not peer.subnet_of(pod):
    raise SystemExit("Pod IP is outside the approved overlay range.")
if int(address) - int(peer.network_address) < 4 or address == peer.broadcast_address:
    raise SystemExit("Pod IP is Azure-reserved in the peer subnet; stop before creating the peer.")
if any(left.overlaps(right) for index, left in enumerate((pod, nodes, recovery, services))
       for right in (pod, nodes, recovery, services)[index + 1:]):
    raise SystemExit("Unexpected overlap outside the intentional pod/peer collision.")
print(peer)
PY
)
printf 'PodIp=%q\nPodUid=%q\nPeerCidr=%q\n' "$PodIp" "$PodUid" "$PeerCidr" >> "$Work/context.sh"
```

If the selected pod address is reserved by Azure's subnet rules, stop; do not force it onto a NIC. Recreate only this disposable client before creating the peer and rerun address discovery, or clean up. Once the peer exists, keep this pod's UID/IP unchanged throughout the experiment.

</details>

## 4. Put a real HTTP service at the colliding address

**Task:** create a peer VNet containing the selected `/24` and the separate recovery subnet. Put the server at the exact pod IP, without opening public ingress or adding a shared-network connection.

<details>
<summary>Solution</summary>

```bash
assert_cluster
az network vnet create -g "$ResourceGroup" -n overlap-peer -l "$Location" \
  --address-prefixes "$PeerCidr" "$RecoveryCidr" -o none
BadSubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name overlap-peer \
  -n collision --address-prefixes "$PeerCidr" --default-outbound-access false --query id -o tsv)
RecoverySubnetId=$(az network vnet subnet create -g "$ResourceGroup" --vnet-name overlap-peer \
  -n recovery --address-prefixes "$RecoveryCidr" --default-outbound-access false --query id -o tsv)
PeerVnetId=$(az network vnet show -g "$ResourceGroup" -n overlap-peer --query id -o tsv)
az network vnet peering create -g "$ResourceGroup" --vnet-name overlap-nodes -n to-peer \
  --remote-vnet "$PeerVnetId" --allow-vnet-access -o none
az network vnet peering create -g "$ResourceGroup" --vnet-name overlap-peer -n to-nodes \
  --remote-vnet "$NodeVnetId" --allow-vnet-access -o none
az network nsg create -g "$ResourceGroup" -n overlap-service-nsg -l "$Location" -o none
az network nsg rule create -g "$ResourceGroup" --nsg-name overlap-service-nsg -n allow-lab-http \
  --priority 100 --direction Inbound --access Allow --protocol Tcp \
  --source-address-prefixes 10.80.0.0/20 10.80.16.0/24 --source-port-ranges '*' \
  --destination-address-prefixes '*' --destination-port-ranges 8080 -o none
az network nsg rule create -g "$ResourceGroup" --nsg-name overlap-service-nsg -n deny-inbound \
  --priority 200 --direction Inbound --access Deny --protocol '*' \
  --source-address-prefixes '*' --source-port-ranges '*' \
  --destination-address-prefixes '*' --destination-port-ranges '*' -o none
az network nic create -g "$ResourceGroup" -n overlap-service-nic -l "$Location" \
  --subnet "$BadSubnetId" --private-ip-address "$PodIp" --network-security-group overlap-service-nsg \
  -o none
ServiceNicId=$(az network nic show -g "$ResourceGroup" -n overlap-service-nic --query id -o tsv)
[[ "$ServiceNicId" == */networkInterfaces/overlap-service-nic ]] ||
  { printf 'Missing service NIC identity; do not create a VM with default networking.\n' >&2; exit 1; }
```

The server uses Ubuntu's installed Python, not a downloaded application or package installation. Its subnet has no general internet egress. Cloud-init enables a non-root service on boot, including after the recovery restart:

```bash
cat > "$Work/service-cloud-init.yaml" <<YAML
#cloud-config
users:
  - default
  - name: overlap-svc
    uid: 10001
    lock_passwd: true
    shell: /usr/sbin/nologin
write_files:
  - path: /srv/overlap/index.html
    permissions: '0644'
    content: |
      $Marker
  - path: /etc/systemd/system/overlap-http.service
    permissions: '0644'
    content: |
      [Unit]
      Description=Synthetic overlap lab HTTP service
      After=network-online.target
      Wants=network-online.target
      [Service]
      User=overlap-svc
      ExecStart=/usr/bin/python3 -m http.server 8080 --bind 0.0.0.0 --directory /srv/overlap
      Restart=on-failure
      NoNewPrivileges=true
      ProtectSystem=strict
      ProtectHome=true
      PrivateTmp=true
      [Install]
      WantedBy=multi-user.target
runcmd:
  - [systemctl, daemon-reload]
  - [systemctl, enable, --now, overlap-http.service]
YAML
az vm create -g "$ResourceGroup" -n overlap-service -l "$Location" \
  --nics "$ServiceNicId" --image Ubuntu2404 --size "$TestVmSize" \
  --admin-username laboperator --ssh-key-values "$Work/vm-key.pub" \
  --custom-data "$Work/service-cloud-init.yaml" \
  --security-type TrustedLaunch --enable-secure-boot true --enable-vtpm true \
  --storage-sku StandardSSD_LRS --os-disk-size-gb 64 -o none
ServiceVmId=$(az vm show -g "$ResourceGroup" -n overlap-service --query vmId -o tsv)
ServiceIpConfig=$(az network nic show -g "$ResourceGroup" -n overlap-service-nic \
  --query 'ipConfigurations[0].name' -o tsv)
printf 'RecoverySubnetId=%q\nServiceVmId=%q\nServiceIpConfig=%q\n' \
  "$RecoverySubnetId" "$ServiceVmId" "$ServiceIpConfig" >> "$Work/context.sh"
```

Require the actual HTTP marker in task 5 before calling this server healthy. Provisioning success alone does not prove cloud-init or the systemd service completed.

</details>

## 5. Prove the difference between peering and pod reachability

**Task:** require healthy peering and a successful request from the control VM, then demonstrate failure from the pod to the same literal destination. Exclude DNS, proxies, a broken service and Kubernetes authorization failures.

<details>
<summary>Solution</summary>

Use the same bounded Python request in both clients. It disables environment proxies, checks HTTP status/body, and accepts only classified network failures for the intentional negative test. An HTTP error, wrong marker, successful response or failed `kubectl exec` is not a passing fault demonstration.

```bash
cat > "$Work/probe.py" <<'PY'
import errno
import ipaddress
import sys
import urllib.error
import urllib.request

host, port_text, marker, expected = sys.argv[1:]
ipaddress.IPv4Address(host)
port = int(port_text)
if not 1 <= port <= 65535 or expected not in ("reachable", "unreachable"):
    raise SystemExit("Invalid probe port or expectation.")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(f"http://{host}:{port}/", timeout=5) as response:
        body = response.read(1024).decode().strip()
        status = response.status
except urllib.error.HTTPError:
    raise
except (urllib.error.URLError, TimeoutError) as error:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    network_failure = isinstance(reason, TimeoutError) or (
        isinstance(reason, OSError) and reason.errno in (
            errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT))
    if expected != "unreachable" or not network_failure:
        raise
    print(f"EXPECTED_NETWORK_FAILURE:{type(reason).__name__}:{reason}")
else:
    if expected != "reachable":
        raise SystemExit(f"Unexpected HTTP response from colliding address: status={status}, body={body!r}")
    if status != 200 or body != marker:
        raise SystemExit(f"Wrong service evidence: status={status}, body={body!r}")
    print(f"SERVICE_OK:{marker}")
PY
probe_control() {
  local address=$1 script result
  assert_lab || return
  python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$address" || return
  [[ "$Marker" =~ ^lab28-[0-9a-f]{16}$ ]] || { printf 'Invalid run marker.\n' >&2; return 1; }
  [[ -s "$Work/probe.py" ]] || { printf 'Restore the reviewed probe.py before sending a Run Command.\n' >&2; return 1; }
  script=$(printf "python3 - '%s' 8080 '%s' reachable <<'PY'\n" "$address" "$Marker"
    cat "$Work/probe.py"
    printf '\nPY\n') || return
  result=$(az vm run-command invoke -g "$ResourceGroup" -n overlap-control \
    --command-id RunShellScript --scripts "$script" -o json) || return
  jq -er '.value[].message' <<< "$result" | tee "$Work/control-last.txt" || return
  grep -Fx "SERVICE_OK:$Marker" "$Work/control-last.txt"
}
assert_probe_identity() {
  local pod
  assert_cluster || return
  pod=$(kubectl get pod -n overlay-overlap overlap-client -o json) || return
  jq -e --arg uid "$PodUid" --arg ip "$PodIp" \
    '.metadata.uid == $uid and .status.podIP == $ip and .status.phase == "Running"' \
    <<< "$pod" >/dev/null || {
    printf 'Diagnostic pod changed; stop rather than claiming the same collision.\n' >&2; return 1;
  }
}
assert_probe_identity
for entry in overlap-nodes:to-peer overlap-peer:to-nodes; do
  az network vnet peering show -g "$ResourceGroup" --vnet-name "${entry%:*}" -n "${entry#*:}" \
    --query '{state:peeringState,remote:remoteAddressSpace.addressPrefixes}' -o json |
    tee "$Work/peering-${entry%:*}.json" | jq -e '.state == "Connected"'
done
az network nic show-effective-route-table -g "$ResourceGroup" -n overlap-control-nic -o json \
  > "$Work/control-routes-before.json"
az network nic show -g "$ResourceGroup" -n overlap-service-nic -o json |
  jq -e --arg ip "$PodIp" '.ipConfigurations[0].privateIPAddress == $ip'
probe_control "$PodIp"
cp "$Work/control-last.txt" "$Work/control-before.txt"
kubectl exec -i -n overlay-overlap overlap-client -- python - "$PodIp" 8080 "$Marker" unreachable \
  < "$Work/probe.py" | tee "$Work/pod-before.txt"
```

Expected: the control VM prints `SERVICE_OK:<run marker>`, while the pod prints `EXPECTED_NETWORK_FAILURE:...`, normally connection refused for this exact self-address collision. Both use the **same destination IP/port**. The control's effective routes show the peer prefix through VNet peering. Save the actual evidence; do not invent a timeout if you observed a refusal.

If the control fails, stop: check VM agent readiness, cloud-init/service startup, NSG TCP 8080 rules, peering and routes. Retry only a bounded number of times after diagnosing startup. If the pod returns any HTTP response, the negative test fails: inspect pod identity, destination and unexpected listeners. Never “fix” the demonstration by adding a deny policy or stopping the server.

For this exact collision, the destination is local to the pod, so the normal external-egress path is not selected. Source NAT cannot turn a conflicting destination into a different address. Do not add a UDR, broad permissions, public access or TLS bypasses to make the unsupported design look supported.

</details>

## 6. Remove the overlap and prove recovery with the same pod and server

**Task:** move the server NIC into the existing nonoverlapping recovery subnet, remove the conflicting subnet/prefix, synchronize peering and repeat both requests. Do not change the cluster's pod CIDR or recreate the diagnostic pod.

<details>
<summary>Solution</summary>

This operation deliberately interrupts the disposable server. Confirm its exact identity and group before moving it. The VNet contains both subnets already; this is a subnet move within one VNet, not an unsupported cross-VNet NIC move.

```bash
assert_probe_identity
[[ $(az vm show -g "$ResourceGroup" -n overlap-service --query vmId -o tsv) == "$ServiceVmId" ]] ||
  { printf 'Service VM identity changed; stop.\n' >&2; exit 1; }
printf '%s' "Type $ResourceGroup to interrupt and renumber only this lab service: " >&2
read -r Approval
[[ "$Approval" == "$ResourceGroup" ]] ||
  { printf 'Recovery change not confirmed.\n' >&2; exit 1; }
az vm deallocate -g "$ResourceGroup" -n overlap-service
az network nic ip-config update -g "$ResourceGroup" --nic-name overlap-service-nic \
  -n "$ServiceIpConfig" --subnet "$RecoverySubnetId" --private-ip-address "$RecoveryIp" -o none
az vm start -g "$ResourceGroup" -n overlap-service
az network vnet subnet delete -g "$ResourceGroup" --vnet-name overlap-peer -n collision
az network vnet update -g "$ResourceGroup" -n overlap-peer --address-prefixes "$RecoveryCidr" -o none
az network vnet peering sync -g "$ResourceGroup" --vnet-name overlap-nodes -n to-peer -o none
az network vnet peering sync -g "$ResourceGroup" --vnet-name overlap-peer -n to-nodes -o none
```

Require the corrected actual address space, not just a successful command:

```bash
az network vnet show -g "$ResourceGroup" -n overlap-peer -o json |
  jq -e --arg prefix "$RecoveryCidr" '.addressSpace.addressPrefixes == [$prefix]'
az network vnet peering show -g "$ResourceGroup" --vnet-name overlap-nodes -n to-peer -o json |
  jq -e --arg prefix "$RecoveryCidr" \
    '.peeringState == "Connected" and .remoteAddressSpace.addressPrefixes == [$prefix]'
az network nic show -g "$ResourceGroup" -n overlap-service-nic -o json |
  jq -e --arg ip "$RecoveryIp" --arg subnet "$RecoverySubnetId" \
    '.ipConfigurations[0].privateIPAddress == $ip and .ipConfigurations[0].subnet.id == $subnet'
[[ $(az vm show -g "$ResourceGroup" -n overlap-service --query vmId -o tsv) == "$ServiceVmId" ]] ||
  { printf 'Service VM identity changed during recovery.\n' >&2; exit 1; }
assert_probe_identity
probe_control "$RecoveryIp"
cp "$Work/control-last.txt" "$Work/control-after.txt"
kubectl exec -i -n overlay-overlap overlap-client -- python - "$RecoveryIp" 8080 "$Marker" reachable \
  < "$Work/probe.py" | tee "$Work/pod-after.txt"
```

Both clients must now print the same `SERVICE_OK:<run marker>`. If the server has not restarted yet, inspect startup and retry the bounded request after it is ready; never ignore a failed probe. The original pod UID/IP, service VM `vmId` and marker remain unchanged. Only the remote network/service address changed.

This is real address-plan repair, not an NSG exception or a claim that overlapping networks are safe. In a customer environment, renumbering may require DNS/client updates, data migration and a change window; replacing the cluster with a nonoverlapping pod CIDR is another design option. This lab deliberately uses literal IPs to isolate routing from DNS. Do not promise in-place replacement of an AKS pod CIDR.

</details>

## Exit evidence

Keep the original cluster network profile, pod UID/IP/image digest, actual peer prefix, both Connected peerings, control VM route evidence and successful control request. Pair these with the **classified pod failure**, the corrected prefix/NIC/peering state, and both successful post-recovery requests with the same service marker.

Explain why an ARM/peering green status and a successful VM request were insufficient. Distinguish independent clusters reusing isolated overlay ranges from a pod trying to reach a real external endpoint inside its own range.

## Customer conversation

<details>
<summary>Model answer: Why does Azure allow the peering if the design is broken?</summary>

The peered VNets' address spaces are different: node space versus the service space. The conflict is with AKS's separate pod overlay, not between the two VNet prefixes. Control-plane provisioning success cannot prove every workload's destination lookup is unambiguous.

</details>

<details>
<summary>Model answer: Would Cilium, outbound NAT or a host-network pod solve this?</summary>

Cilium does not remove Azure CNI Overlay address-planning constraints. Ordinary outbound SNAT changes source addresses, not destinations. A host-network pod shares a Kubernetes node's routing/data plane and is not an independent Azure routing control. This lab deliberately uses a separate VM to establish the contrast.

</details>

<details>
<summary>Model answer: Does every partial overlap reproduce this exact error?</summary>

No. This exercise deliberately collides with the client's own IP to make the symptom deterministic. Other destinations may be intercepted, dropped or behave differently depending on allocated overlay blocks and implementation. Any surprising success is evidence to investigate, not permission to use an unsupported overlapping design.

</details>

## Re-entry and cleanup

The local `context.sh` contains identifiers, not tokens. On re-entry, set `Work` to the exact printed folder, enable strict mode, source `"$Work/context.sh"`, and redefine only the guard/probe functions above. Verify the approved Azure context; do not silently switch subscriptions. Recover any derived IDs missing after an interrupted setup using `az ... show` for this tagged group, not by rerunning create blocks.

Reconnect with the explicit-target Bastion activation in task 3, not the cumulative settings. Keep the pod UID/IP evidence: if it was replaced, stop and repeat the isolated experiment from a newly approved boundary rather than pretending the original collision still exists. If recovery stopped partway, inspect the VM power state, NIC subnet/IP, peer subnets/prefixes and peering synchronization, then finish only the remaining steps. If the allocation deadline has passed, proceed to teardown rather than buying more diagnostic time.

**Task:** save the intended evidence and delete only this experiment. Keep Bastion until API inspection is complete. Failed/partial setup still incurs charges; cleanup does not require successful pod access.

<details>
<summary>Solution</summary>

Stop using the API and disconnect this shell's tunnel if active, then verify the group ownership again and explicitly confirm deletion:

```bash
if [[ -n ${AKS_BASTION_PID:-} ]]; then
  disconnect_aks_bastion
fi
assert_lab
az resource list -g "$ResourceGroup" --query '[].{name:name,type:type}' -o table
printf '%s' "Type $ResourceGroup to permanently delete ONLY this overlap experiment: " >&2
read -r Approval
[[ "$Approval" == "$ResourceGroup" ]] ||
  { printf 'Cleanup not confirmed.\n' >&2; exit 1; }
az group delete -n "$ResourceGroup" --yes
GroupExists=$(az group exists --name "$ResourceGroup")
[[ "$GroupExists" == false ]] ||
  { printf 'Resource group deletion is not complete; inspect remaining resources.\n' >&2; exit 1; }
```

AKS owns its managed node resource group: discover/record it before deleting the cluster, and verify its cleanup afterward rather than deleting unrelated VM scale sets manually. The foundation was never deployed here, and no cumulative resources should be touched. Check retained disks, NAT/Bastion public IPs, role assignments and costs after deletion. Preserve only intended evidence, then remove the specific disposable local files/private key in the printed work folder; never delete the repository or another session's files.

</details>

## References and support

[Azure CNI Overlay IP planning and overlap restrictions](https://learn.microsoft.com/azure/aks/concepts-network-azure-cni-overlay#ip-address-planning), [Cilium IPAM options](https://learn.microsoft.com/azure/aks/azure-cni-powered-by-cilium#ip-address-management-ipam-with-azure-cni-powered-by-cilium), [AKS NAT Gateway](https://learn.microsoft.com/azure/aks/nat-gateway), [NIC subnet/IP changes](https://learn.microsoft.com/azure/virtual-network/virtual-network-network-interface#change-network-interface-settings), [peering address-space changes](https://learn.microsoft.com/azure/virtual-network/update-virtual-network-peering-address-space), [Linux Run Command](https://learn.microsoft.com/azure/virtual-machines/linux/run-command), [Bastion native AKS access](https://learn.microsoft.com/azure/bastion/bastion-connect-to-aks-private-cluster).

The overlapping stage intentionally violates the supported address plan. Region/SKU availability, deployment, packet behavior and recovery must be measured by the learner; offline command checks are not live validation. Native API access retains the repository's explicit preview dependency.

---

**Next lab (optional):** [Lab 29 - Switch API access from public to private](29-api-private-connectivity.md).
