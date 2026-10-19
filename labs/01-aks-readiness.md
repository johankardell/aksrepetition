# Lab 1 - Decide whether AKS is ready for your workload

**Level:** beginner. **Scenario:** you are taking ownership of an AKS environment and must explain its responsibilities, prerequisites and bill before creating a cluster.

**Outcome:** an approved platform decision, supported regional version and capacity choice, and validated local bootstrap configuration. This is AKS operations practice, not a tour of Kubernetes objects.

## Prerequisites and resource boundary

Use a dedicated subscription and a Linux workstation with Bash 5+, Git, `jq`, Azure CLI 2.90+, Bicep and OpenSSH. Run commands from the repository root. You need resource deployment and role-assignment authority, plus membership in an existing Entra security group approved for cluster administration. Subscription ownership does not grant directory group-creation rights.

No cluster is deployed in this lab. The README bootstrap creates a dedicated resource group and local key/configuration files. **Lab 2 deploys the whole enterprise foundation**, not a low-cost starter cluster: six AKS nodes, a private build VM, Bastion Standard, Azure Firewall Standard, Premium ACR and Service Bus, Key Vault and Log Analytics. Later beginner labs reuse these resources. Obtain approval for the complete footprint first; budgets are alerts, not spending caps.

Already have a foundation or progressed checkout? Do not overwrite settings, regenerate keys or start a second environment. Use [lab 10's entry paths](10-baseline.md#entry-paths) to identify where to resume.

## Directives

### 1. Explain what you are buying and operating

**Task:** compare AKS with Container Apps/App Service. Sketch the management, image and application traffic paths in `infra/main.bicep`. Assign an owner to every path.

<details>
<summary>Solution</summary>

Choose AKS when Kubernetes APIs, ecosystem or platform controls justify the operating burden. A container image alone is not a reason to choose Kubernetes.

| Boundary | Azure provides | You still own |
|---|---|---|
| Managed control plane | Kubernetes API/control-plane service | Supported versions, access policy, upgrade decisions and workload compatibility |
| Node pools | Managed provisioning of pool VMs | SKU/quota, pool design, capacity, update policy and disruption planning |
| Identity | Entra authentication and managed identities | Correct role, principal and resource scope; application authorization |
| Networking | Azure network services | Address plan, private DNS, routes, ingress/egress and reachability evidence |
| Application | A place to schedule containers | Image safety, resources, probes, business health, data recovery and SLO |

Distinguish **Standard cluster mode** (operator-controlled configuration), **Standard pricing tier** (control-plane service terms), and the application's **SLO** (user-visible reliability). None means that every order is processed successfully. AKS Automatic provides more managed defaults; Standard is used here to expose operational controls, not because manual operation is always better.

Draw four separate arrows: operator -> Bastion -> private API; kubelet -> ACR; API -> Service Bus -> worker; operator -> Bastion SSH -> private build VM. Workload identities authorize Azure dependency calls, not end users. The sample has no customer authentication; use synthetic data only.

Azure creates a separate **node resource group** containing managed infrastructure. Discover its name after deployment; do not edit its VM scale sets or delete it independently to resize or repair AKS.

</details>

### 2. Select a region, supported version and capacity envelope

**Task:** complete the [README bootstrap configuration](../README.md#bootstrap-configuration), including editing every `REPLACE` value, region/version discovery, creating the dedicated group, and creating the two distinct RSA key pairs. Keep existing files intact.

<details>
<summary>Solution</summary>

Use the README's configuration block as the single schema, rather than creating a different beginner settings format. Choose a private lab hostname now for later networking work; you do not need to publish it in this lab. Select a distinct secondary region for the later capstone.

After bootstrap, verify the active context and recheck the regional choices:

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az account show --query '{subscription:id,tenant:tenantId,name:name}' -o json
az aks get-versions --location "$(lab_value Location)" -o table
az vm list-usage --location "$(lab_value Location)" -o table
az vm list-skus --location "$(lab_value Location)" --size "$(lab_value VmSize)" --all -o json
az vm list-skus --location "$(lab_value Location)" --size "$(lab_value BuildVmSize)" --all -o json
test -s ./rendered/keys/aks.pub
test -s ./rendered/keys/build-vm.pub
```

Select an actual supported **GA patch**, not a preview, and keep `kubectl` within one minor of the API server when installed. The provisioned build VM supplies the runtime tools in lab 3. Use Azure Linux 3, not retired Azure Linux 2.0. A SKU listing is not a reservation: inspect restrictions, zone support and subscription quota.

The default pool footprint is six four-vCPU nodes: 24 node vCPUs before upgrade surge or scaling, plus two B-series vCPUs for the build VM. Total regional and VM-family quotas both matter. Record headroom for later changes rather than merely proving six nodes fit today.

Inspect the template address plan: spoke `10.40.0.0/16`, hub `10.50.0.0/16`, overlay pods `192.168.0.0/16`, services `172.20.0.0/16`. Check connected networks for overlap before initial deployment; do not improvise an in-place migration later.

</details>

### 3. Prove permission and cost readiness

**Task:** identify the administrator group and distinguish resource deployment rights from role-assignment and Kubernetes rights. Get an owner to approve the [full resource inventory](../README.md#before-spending-anything).

<details>
<summary>Solution</summary>

Record, without tokens or private keys: the dedicated group, region, selected version/SKUs, quota evidence, intended Entra administrator group, deployment/role-assignment authority, cleanup owner and approved footprint. Confirm group membership through your directory administrator if you cannot read it yourself.

Contributor can deploy resources but cannot create role assignments. A typical bootstrap operator needs Contributor plus Role Based Access Control Administrator at approved scopes. This temporary provisioning authority is not the permission model for applications or image publishing.

Create Cost Management budgets and notifications through the approved subscription process. Include Firewall, Bastion, Service Bus Premium, registry storage and logs, not just node VMs. Disconnecting a terminal will not remove those charges.

Classify these preflight failures:

| Evidence | Correct next action |
|---|---|
| `use-lab.sh` reports the wrong subscription/tenant | Verify settings and deliberately sign into/select the approved context, then retry validation |
| Version is unavailable or preview-only | Choose a supported regional GA patch; do not force an obsolete example version |
| SKU restrictions or insufficient family quota | Request quota/capacity approval or redesign before deployment |
| Directory group cannot be verified | Ask the directory owner; do not substitute a personal principal or enable local admin credentials |
| Incomplete local key pair | Recover the intended pair; do not overwrite either half |

</details>

## Exit evidence

Keep a short private operator worksheet with the AKS decision, responsibility map, complete cost boundary, supported version, region/SKU/zone/quota checks, nonoverlapping address plan, validated settings and separate public keys. An unresolved permission, support or budget question is a **no-go**, not permission to deploy and see what happens.

## Customer conversation

<details>
<summary>Model answer: If Azure manages AKS, why do we need an operator?</summary>

Azure runs the managed control plane and provisioning machinery. The customer still owns workload behavior, capacity choices, identities, networking, lifecycle policy, observability and recovery.

</details>

<details>
<summary>Model answer: Is this beginner environment inexpensive or production-ready?</summary>

Neither is implied. The learning steps are beginner-friendly, but they share the full enterprise lab foundation. Its initial authenticated public dependency endpoints and unauthenticated sample application are deliberate lab limitations.

</details>

## Cleanup and next step

No foundation exists yet. Retain settings/public keys and continue to [lab 2](02-provision-aks.md). Do not commit keys, settings or subscription evidence. If abandoning the exercise, inspect the dedicated group before using the [README final cleanup](../README.md#pausing-resuming-and-final-cleanup); never delete a group merely because its name resembles an example.

## References

[AKS operator best practices](https://learn.microsoft.com/azure/aks/best-practices), [AKS baseline](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks), [supported versions](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions), [AKS quotas](https://learn.microsoft.com/azure/aks/quotas-skus-regions), [Cost Management budgets](https://learn.microsoft.com/azure/cost-management-billing/costs/tutorial-acm-create-budgets). Discover current support and capacity at execution; no live availability or billing is certified here.
