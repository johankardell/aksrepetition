# Lab 2 - Provision AKS deliberately and verify the result

**Level:** beginner. **Scenario:** the platform design is approved; you must turn it into a repeatable deployment and prove what Azure actually created.

**Outcome:** one verified foundation, with private AKS, separate node pools, managed identities and the management infrastructure needed by subsequent labs.

## Lab contract

| Field | This lab |
|---|---|
| Entry | Lab 1 go/no-go approved; initial bootstrap only. |
| Host and identity | Original workstation; authorized infrastructure/role-assignment operator with existing settings and public keys. |
| Time | Estimate: 45–60 minutes active plus 30–90 minutes Azure provisioning wait. |
| Cost | Starts six nodes, private build VM, Bastion, Firewall, Premium dependencies and logs; approve the complete inventory. |
| Changes | Infrastructure operator owns initial foundation and its deployment record; AKS owns the managed node group. |
| Exit and re-entry | Require successful deployment and inventory. Diagnose exact failed operations before an initial retry; never replay over a progressed stage. |

Use the [session, evidence and cost operating contract](../docs/operations.md) and [stage map](../docs/state.md). Times are planning estimates, not measured results.

## Prerequisites and resource boundary

Complete [lab 1](01-aks-readiness.md). Run from the repository root on the original Linux bootstrap workstation in Bash with Azure CLI/Bicep, `jq` and the local settings/key pairs. The operator needs deployment and role-assignment authority.

**Billable:** this is where the entire [foundation cost inventory](../docs/operations.md#before-spending-anything) starts. Six nodes, Bastion, Firewall, the build VM and Premium dependencies are created together. Do not apply until approved. There is no smaller beginner template.

This lab is for the **first** foundation deployment. If it already succeeded, use the inspection tasks only. If later labs have changed the environment, return to their re-entry guidance; never rerun initial foundation to repair a progressed cluster.

## Directives

### 1. Check providers and inspect what-if

**Task:** verify the required Azure providers, then inspect every resource in the foundation what-if before agreeing to create anything.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
Providers=(
  Microsoft.ContainerService Microsoft.Compute Microsoft.Network Microsoft.ManagedIdentity
  Microsoft.ContainerRegistry Microsoft.KeyVault Microsoft.ServiceBus
  Microsoft.OperationalInsights Microsoft.Insights
)
for provider in "${Providers[@]}"; do
  az provider show --namespace "$provider" --query '{provider:namespace,state:registrationState}' -o json
done
```

If a provider is not registered, obtain the required subscription authority and register that provider before continuing, as in [lab 10 task 2](10-baseline.md#2-discover-then-select-a-supported-version). Registration is a subscription operation, not a fix for an unrelated permission failure.

```bash
bash ./scripts/deploy-foundation.sh
```

Without `--apply`, the helper runs **what-if only**. Check the resource group, names, address ranges, six node VMs, build VM, Bastion Standard/two scale units, Firewall, public IPs, Premium service tiers and scoped role assignments. The public IPs belong to network infrastructure; neither the build VM nor AKS API is made publicly reachable.

What-if is not a capacity reservation or an end-to-end validation. Unexpected resources or modifications to an existing environment are a stop condition.

</details>

### 2. Apply once, after explicit approval

**Task:** deploy the reviewed initial foundation using the existing helper's confirmation gate.

<details>
<summary>Solution</summary>

```bash
# First deployment only, after reviewing what-if and approving its costs.
# Type the exact ResourceGroup from local.settings.json at the prompt.
bash ./scripts/deploy-foundation.sh --apply --confirm
source ./scripts/use-lab.sh
az deployment group show -g "$(lab_value ResourceGroup)" -n foundation \
  --query '{state:properties.provisioningState,outputs:properties.outputs}' -o json
```

Expected: `Succeeded`, with usable foundation outputs. The helper performs what-if again before asking for the exact group name. Do not suppress that confirmation.

The build-VM deployment waits for cloud-init/tooling and a rootless Podman check. A failed readiness extension is a deployment failure, not permission to treat the host as ready.

</details>

### 3. Match actual resources to the design

**Task:** discover the managed node resource group and inspect cluster, pools, networking, identities and management resources without modifying them.

<details>
<summary>Solution</summary>

```bash
source ./scripts/use-lab.sh
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{id:id,state:provisioningState,nodeResourceGroup:nodeResourceGroup,version:kubernetesVersion,sku:sku,private:apiServerAccessProfile,localAccountsDisabled:disableLocalAccounts,aad:aadProfile,network:networkProfile,identity:identity,kubelet:identityProfile.kubeletidentity}' -o json
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{name:name,mode:mode,count:count,sku:vmSize,os:osSKU,zones:availabilityZones,state:provisioningState}' -o table
NodeResourceGroup=$(az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query nodeResourceGroup -o tsv)
test -n "$NodeResourceGroup"
az resource list -g "$NodeResourceGroup" --query '[].{name:name,type:type}' -o table
az network bastion show --ids "$(output_value bastionId)" \
  --query '{id:id,sku:sku.name,scaleUnits:scaleUnits,tunneling:enableTunneling,state:provisioningState}' -o json
az vm show --ids "$(output_value buildVmId)" \
  --query '{id:id,size:hardwareProfile.vmSize,state:provisioningState}' -o json
```

Require private cluster enabled, public FQDN disabled, local accounts disabled, managed Entra/Azure RBAC, Azure CNI Overlay/Cilium, and two pools of three Azure Linux 3 nodes. Confirm Bastion Standard has two scale units and tunneling enabled. Pool provisioning success is not yet a successful authenticated Kubernetes request; prove that in lab 3.

The node resource group is AKS-managed infrastructure, not a second independently operated cluster. Use AKS APIs for pool operations; do not manually scale its VM scale sets. Deployment-derived names come from the helper/outputs, not guessed suffixes.

**Private API does not mean all data paths are private.** ACR, Service Bus and Key Vault still use authenticated public endpoints. Lab 12 creates Private Link and disables them. The build VM's egress uses Firewall now; node egress changes in lab 12. Never attach the node/management UDR to the Bastion subnet.

</details>

### 4. Diagnose a failed deployment without widening permissions

**Task:** identify which operation failed and explain whether a retry is appropriate. If your deployment succeeded, inspect the commands and classify the examples; do not deliberately break infrastructure.

<details>
<summary>Solution</summary>

```bash
az deployment operation group list -g "$(lab_value ResourceGroup)" -n foundation \
  --query "[?properties.provisioningState=='Failed'].{resource:properties.targetResource,message:properties.statusMessage}" -o json
az deployment group list -g "$(lab_value ResourceGroup)" \
  --query '[].{name:name,state:properties.provisioningState}' -o table
```

For a nested module failure, inspect operations for the exact failed deployment name from the output. Read the error's resource scope and code, not just the top-level failure.

| Failure class | Response |
|---|---|
| Authorization denied | Check the operator's role and assignment scope; do not grant every identity Owner |
| Quota/SKU/zone unavailable | Resolve quota/capacity with the subscription owner before retrying |
| Identity role propagation | Verify principal and scope, allow propagation, then reassess |
| `RoleDefinitionDoesNotExist` | Check the actual built-in role ID; waiting cannot correct a wrong ID |
| Build VM readiness failed | Inspect extension/cloud-init evidence; do not skip the readiness check |

An initial failed/canceled deployment may have partial resources and no outputs. `use-lab.sh` can load bootstrap settings for diagnosis in that state. After correcting the cause, review what-if again before an explicitly approved retry of **initial bootstrap only**. Do not delete the group as a troubleshooting shortcut. If metadata is malformed or a supposedly successful deployment has no usable outputs, stop and investigate.

</details>

## Exit evidence

Retain the successful deployment state, selected version/pools, managed node-group identity, private API/Entra/network configuration, Bastion and build-host inventory, and any failure/root-cause record. Do not save access tokens or private keys in evidence.

## Customer conversation

<details>
<summary>Model answer: Why not just rerun the template whenever AKS looks wrong?</summary>

This template is an initial snapshot. Later labs intentionally change networking, add-ons, scaling and versions. Replaying it can undo those changes. Diagnose the owning resource and apply a focused, reviewed correction instead.

</details>

<details>
<summary>Model answer: Does a successful ARM deployment prove the application works?</summary>

No. It proves Azure completed resource provisioning. Authenticated API access, image pulls, scheduling, probes and business processing require separate evidence.

</details>

## Cleanup and next step

Keep the foundation for [lab 3](03-access-and-identity.md). Do not delete nodes, identities or either resource group between exercises. Charges continue while you pause. If ending permanently at this stage, use the [beginner-only teardown](../docs/operations.md#beginner-only-teardown), not later database/Fleet cleanup for resources you have not created.

## References

[Bicep what-if](https://learn.microsoft.com/azure/azure-resource-manager/bicep/deploy-what-if), [deployment operations](https://learn.microsoft.com/azure/azure-resource-manager/templates/deployment-history), [AKS managed identities](https://learn.microsoft.com/azure/aks/use-managed-identity), [AKS resource groups](https://learn.microsoft.com/azure/aks/faq#why-are-two-resource-groups-created-with-aks). Verify live support and your own deployment evidence; these commands are not a certification of subscription readiness.

---

**Next lab:** [Lab 3 - Access and identity](03-access-and-identity.md).
