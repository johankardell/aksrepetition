# Lab 3 - Reach private AKS and prove who you are

**Level:** beginner. **Scenario:** Azure shows a healthy cluster, but `kubectl` fails. Determine whether the problem is connectivity, authentication or authorization before changing permissions.

**Outcome:** verified private API access through native Bastion, a usable private build host, and an identity/traffic map that separates management from Azure data access.

## Prerequisites and resource boundary

Complete [lab 2](02-provision-aks.md). Reuse its Bastion, VM and identities; no additional Azure resources are needed. Their standing charges continue. You need Bastion resource access, user credential access to AKS and the configured Entra group's Kubernetes authorization. A VM login does not grant Azure roles.

**Explicit preview gate:** native AKS tunneling uses `aks-preview`, even though the foundation targets supported GA platform features. Use the reviewed extension versions below; verify the command shape and current support before connecting. Do not substitute examples using admin credentials.

## Directives

### 1. Separate three permission questions

**Task:** distinguish "can I see AKS in Azure?", "can I reach/authenticate to its API?", and "can I perform this operation in this namespace?"

<details>
<summary>Solution</summary>

| Principal/path | Purpose | Does not imply |
|---|---|---|
| Human Azure resource role | Read/manage permitted ARM resources | Kubernetes write access |
| Human Entra identity + AKS Azure RBAC | Authenticate and authorize Kubernetes operations | Private network connectivity |
| Cluster managed identity | Manage required Azure infrastructure | Application data permissions |
| Kubelet managed identity | Pull images from this ACR | Human push rights or Service Bus access |
| API/worker workload identities | Access narrowly scoped Azure dependencies | Customer authentication or cluster administration |

The API and worker have distinct identities. Inspect their principal/client IDs through foundation outputs without printing projected tokens. Detailed federation and negative Azure-data authorization tests belong to [lab 11](11-identity.md).

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{cluster:identity,kubelet:identityProfile.kubeletidentity,aad:aadProfile}' -o json
printf 'API client: %s\nWorker client: %s\n' "$(output_value apiClientId)" "$(output_value workerClientId)"
```

</details>

### 2. Open the private build-host workflow

**Task:** connect from the original workstation, transfer only the required configuration/public keys, and sign into Azure from the VM's checkout.

<details>
<summary>Solution</summary>

On the workstation, install/verify the reviewed native-client extension:

```bash
az extension add --name bastion --version 1.4.3 --upgrade
bash ./scripts/connect-build-vm.sh
```

Follow [README private build VM](../README.md#private-build-vm) through the clone, host-key verification, Bastion transfer tunnel and `scp` recipe, then close the transfer tunnel. Stop before its final Azure login/API activation block: the steps below perform login, and task 3 owns activation. Transfer the existing `local.settings.json` and **public** `aks.pub`/`build-vm.pub` files; keep both private keys on the original workstation. Do not create a new configuration just because you changed hosts.

The VM has no public IP or broad managed-identity privileges. `labbuilder` is a Linux user, not an Azure service principal. The readiness extension has installed the tools; verify the version record:

```bash
cat /var/lib/aks-lab/tool-versions.txt
```

From the repository root **on the VM**, authenticate interactively to the approved tenant/subscription:

```bash
set -euo pipefail
settings=$(jq -e . ./local.settings.json)
az login --use-device-code --tenant "$(jq -er .TenantId <<< "$settings")"
az account set --subscription "$(jq -er .SubscriptionId <<< "$settings")"
source ./scripts/use-lab.sh
az extension show --name aks-preview --query version -o tsv
az extension show --name bastion --query version -o tsv
az aks bastion tunnel --help
```

The reviewed versions are `aks-preview` **22.0.0b5** and `bastion` **1.4.3**. If missing/mismatched, install the reviewed versions as in [lab 10 task 4](10-baseline.md#4-connect-through-foundation-bastion-and-prove-private-api-access-with-entra), then verify again. Do not downgrade blindly if current support no longer matches; resolve the documented command/support boundary first.

Run the remaining mixed Azure/Kubernetes/application commands on this VM. A separate workstation-only API tunnel is possible, but it will not provide the private data routing needed later.

</details>

### 3. Verify API connectivity, TLS and authorization

**Task:** activate a current-shell tunnel and prove both the intended endpoint and a successful authenticated request.

<details>
<summary>Solution</summary>

```bash
source ./scripts/activate-aks-bastion.sh
```

Keep this Bash shell open. The helper supplies a temporary mode-600 kubeconfig, preserving the private hostname/CA for TLS and using Entra user credentials. It does not replace your default kubeconfig.

```bash
set -euo pipefail
source ./scripts/use-lab.sh
: "${KUBECONFIG:?Activate the native Bastion session first}"
fqdn=$(az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --query privateFqdn -o tsv)
TunnelConfig=$(kubectl config view --minify --raw -o json)
jq -e --arg fqdn "$fqdn" '
  .clusters[0].cluster as $cluster |
  $cluster.server == "https://localhost:8443/"
  and $cluster["tls-server-name"] == $fqdn
  and ($cluster["insecure-skip-tls-verify"] // false) == false
  and ($cluster["certificate-authority-data"] | type == "string" and length > 0)
  and .users[0].user.exec.command == "kubelogin"' <<< "$TunnelConfig"
unset TunnelConfig
kubectl config current-context
kubectl --request-timeout=30s auth can-i create deployments --namespace orders
kubectl --request-timeout=30s get nodes -L agentpool,topology.kubernetes.io/zone,kubernetes.azure.com/os-sku
```

Require `yes` for the intended operator and six Ready nodes. This lab uses default port 8443; if you deliberately selected another local port, change the expected loopback endpoint only.

Do not print/store raw kubeconfigs in evidence. Record the successful checks and resource/context identity instead. Never fetch credentials over the active tunnel kubeconfig, disable TLS verification, or enable local accounts to "fix" connectivity.

</details>

### 4. Diagnose the path, then practice reconnecting

**Task:** compare private DNS and data access from the VM with the API tunnel. Classify failures without deliberately revoking shared roles.

<details>
<summary>Solution</summary>

On the VM, with settings still loaded:

```bash
dig +short "$fqdn"
getent ahostsv4 "$fqdn"
getent ahostsv4 "$(lab_value RegistryServer)"
getent ahostsv4 "$(lab_value ServiceBusName).servicebus.windows.net"
```

The AKS FQDN resolves privately on the VNet-connected host. Dependency data endpoints are still authenticated/public until lab 12; do not claim these DNS checks already prove Private Link. Successful DNS also does not prove TLS, authentication or authorization.

| Symptom | Investigate first |
|---|---|
| Loopback connection refused | Tunnel owner process/session and selected local port |
| Entra/kubelogin token acquisition error | Tenant, user sign-in, CLI/extension version and credentials |
| Kubernetes `Forbidden` | Authenticated principal, operation, namespace and role propagation |
| Registry/application timeout with a working API | That data path's DNS/routes/firewall, not Kubernetes RBAC |
| Hostname/CA verification failure | Intended cluster and original CA/hostname; never disable verification |

From an external workstation, the remote tunnel can reach the API even without local private DNS/routing. Bastion is **not a VPN**: it does not route workstation `podman`, database traffic or arbitrary application HTTPS into the VNet.

Practice the required reconnect cycle in the owning shell:

```bash
disconnect_aks_bastion
source ./scripts/activate-aks-bastion.sh
kubectl --request-timeout=30s get nodes
```

Repeat task 3's TLS/context checks after reconnecting. A second terminal on the same host may export the owner's exact current temporary `KUBECONFIG`, as in [README shared sessions](../README.md#bastion-sessions-and-data-plane-access). That file is unusable after disconnect; do not copy it to another host.

</details>

## Exit evidence

Record the reviewed extension versions, Bastion identity/SKU, TLS/Entra checks, successful operator authorization and node request, separate private DNS evidence, and identity/traffic map. Be able to identify which host runs each later command.

## Customer conversation

<details>
<summary>Model answer: Why does Reader on the cluster not make kubectl work?</summary>

Azure resource read access, user credential retrieval, API reachability, Entra authentication and Kubernetes authorization are separate checks. Solve the failing layer rather than granting a broad role.

</details>

<details>
<summary>Model answer: Can a hosted CI runner push to our private ACR through this AKS tunnel?</summary>

No. The tunnel serves the Kubernetes API only. Registry publishing requires its own authorized identity and private data-plane routing/DNS; later GitOps work uses an approved private runner.

</details>

## Cleanup and next step

Continue to [lab 4](04-node-pools-and-capacity.md) in the active VM shell, or disconnect explicitly when pausing. Close the separate transfer tunnel when its work is finished. Leave Azure resources intact; sessions ending do not stop billing. Use [beginner-only teardown](../README.md#beginner-only-teardown) only when permanently abandoning this environment.

## References

[AKS access and identity](https://learn.microsoft.com/azure/aks/concepts-identity), [Azure RBAC for Kubernetes authorization](https://learn.microsoft.com/azure/aks/manage-azure-rbac), [private AKS](https://learn.microsoft.com/azure/aks/private-clusters), [Bastion native SSH](https://learn.microsoft.com/azure/bastion/connect-vm-native-client-linux). Native AKS tunneling retains the explicit preview exception; verify its supported command shape rather than copying older admin-credential examples.
