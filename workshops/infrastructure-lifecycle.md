# Infrastructure lifecycle beyond bootstrap

## Workshop contract

| Field | Contract |
|---|---|
| Entry | Lab 22's lifecycle decisions; understand the [state map](../docs/state.md). Offline fixture path needs no Azure environment. |
| Host and identity | Linux, Bash or Zsh, jq, Python, Git; optional live inventory uses only approved Azure/Kubernetes read access. |
| Time | 60-90 minutes active; optional export may take minutes. No provisioning wait on the required path. |
| Cost | No additional Azure resources. Existing resources still bill if using the live inventory path. |
| Changes | Ignored local state projection and replacement runbook only; no template application, upgrade, deletion or network change. |
| Exit and re-entry | Classified drift, reviewed owned intent, clean offline comparison, replacement/fencing/rollback evidence plan. This is not a deployed replacement cluster. |

**Scenario:** a teammate proposes rerunning foundation to make infrastructure reproducible.
It would restore obsolete values. Reconcile what the operator intends to own, rather than
copying every observed field or treating the live cluster as the desired-state source.

## 1. Identify dangerous drift

**Task:** compare the initial intent with this **synthetic post-22** observation. Distinguish
intentional progression, dynamic state and a suspicious deviation. Do not call it a real
Azure inventory.

**Required evidence:** owner and retain/revert/investigate decision for every difference.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
mkdir -p .artifacts/lifecycle
cat > .artifacts/lifecycle/bootstrap.json <<'JSON'
{
  "kubernetesVersion": "SYNTHETIC-old",
  "outboundType": "loadBalancer",
  "privateApi": true,
  "appsAutoscaler": false,
  "appsMin": null,
  "appsMax": null,
  "appsCount": 3,
  "dependencyPublicAccess": true,
  "addons": ["csi", "logs"]
}
JSON
cat > .artifacts/lifecycle/observed.json <<'JSON'
{
  "kubernetesVersion": "SYNTHETIC-upgraded",
  "outboundType": "userDefinedRouting",
  "privateApi": true,
  "appsAutoscaler": true,
  "appsMin": 3,
  "appsMax": 4,
  "appsCount": 4,
  "dependencyPublicAccess": false,
  "addons": ["csi", "logs", "gateway", "metrics", "keda", "policy", "defender"]
}
JSON
diff -u <(jq -S . .artifacts/lifecycle/bootstrap.json) \
  <(jq -S . .artifacts/lifecycle/observed.json) || test "$?" -eq 1
```

Retain the reviewed upgrade target (22), UDR/private dependencies (12), autoscaling bounds (15)
and add-ons (12-20). Keep the private API. `appsCount` is a current observation under CA,
not a fixed desired count to impose on every reconcile. A live value unsupported by a
reviewed change would require investigation, not automatic adoption.

The `SYNTHETIC-*` strings intentionally cannot be deployed as Kubernetes versions. Never feed
these fixture files to Azure. The projection is a comparison exercise, not ARM parameters.

</details>

For an optional real **read-only** inventory, use the original private-host session:

```bash
source ./scripts/use-lab.sh
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{id:id,version:kubernetesVersion,api:apiServerAccessProfile,network:networkProfile,addons:addonProfiles,security:securityProfile,autoscalers:workloadAutoScalerProfile}' \
  -o json > .artifacts/lifecycle/cluster-observed.json
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{name:name,version:orchestratorVersion,image:nodeImageVersion,count:count,min:minCount,max:maxCount,autoscaler:enableAutoScaling,upgrade:upgradeSettings}' \
  -o json > .artifacts/lifecycle/pools-observed.json
kubectl get deployment,hpa,scaledobject -n orders -o json > .artifacts/lifecycle/workload-observed.json
flux get kustomizations -A
```

These selected outputs contain infrastructure identifiers; keep them private and uncommitted.
Supplement with the named focused deployment outputs and records from 12-22. Neither this
projection nor `az group export` is a complete redeployable specification.

## 2. Consolidate only owned intent

**Task:** write a local desired projection that retains reviewed progression while excluding
dynamic count. Require the same owned fields to match the observed fixture, then deliberately
introduce one old value to prove the comparison catches a destructive replay.

**Required evidence:** matching owned state, expected drift rejection, and a field-to-module
ownership ledger. Do not simply copy live JSON into an IaC template.

<details>
<summary>Solution</summary>

After reviewing the differences, author the small owned projection:

```bash
cat > .artifacts/lifecycle/desired.json <<'JSON'
{
  "kubernetesVersion": "SYNTHETIC-upgraded",
  "outboundType": "userDefinedRouting",
  "privateApi": true,
  "appsAutoscaler": true,
  "appsMin": 3,
  "appsMax": 4,
  "dependencyPublicAccess": false,
  "addons": ["csi", "logs", "gateway", "metrics", "keda", "policy", "defender"]
}
JSON
jq -S 'del(.appsCount)' .artifacts/lifecycle/observed.json > .artifacts/lifecycle/owned-observed.json
diff -u <(jq -S . .artifacts/lifecycle/desired.json) .artifacts/lifecycle/owned-observed.json
jq '.outboundType = "loadBalancer"' .artifacts/lifecycle/desired.json > .artifacts/lifecycle/replayed.json
Status=0
diff -u <(jq -S . .artifacts/lifecycle/replayed.json) .artifacts/lifecycle/owned-observed.json || Status=$?
test "$Status" -eq 1
```

| Intended field | Current authority | Consolidation requirement |
|---|---|---|
| AKS version, networking and add-ons | Focused platform changes recorded by 12-22 | One maintained cluster definition with current writable properties and no competing owner. |
| Subnets/UDRs/peering | Network modules and approved associations | Preserve every subnet and route association; do not let a partial VNet PUT remove others. |
| Private dependency flags and endpoints | Focused service/network ownership | Stage DNS/reachability before disabling public access; never reopen to hide a failure. |
| Apps capacity | CA enabled/min/max | Do not continually force observed count; preserve surge/IP/quota budget. |
| App specifications | Reviewed app Git | Keep out of platform ARM; preserve namespace-scoped roles. |
| Database endpoint after failback | Latest reviewed DB patch and inventory | Not the stale original `postgres` deployment output. |

For a production consolidation PR, copy the maintained source modules into a separately owned
design, update all intended properties and versioned inputs, review resource identities and
API PUT semantics, then test on an **isolated replacement**. Do not apply the lab's initial
foundation to the progressed primary. A successful local compile is syntax/type evidence,
not proof of a safe incremental update.

</details>

## 3. Rehearse replacement and rollback as a state machine

**Task:** design a same-region blue/green cluster replacement without cloning two writable
databases. Use the dependency/identity patterns from lab 23, but distinguish cluster replacement
from database/message regional promotion.

**Required evidence:** a table with an owner, precondition, action, success signal and abort
behavior for each state below. Supply specific resource/context names from your own inventory
only if doing a real approved plan; the required exercise does not provision them.

<details>
<summary>Model answer</summary>

| State | Gate and action | Abort / rollback boundary |
|---|---|---|
| Prepare | Approve replacement budget/address plan/version and independently reachable API/data paths. Compile reviewed full intent; review what-if only under separate live approval. | Old cluster remains serving; discard only explicitly identified unconsumed replacement resources. |
| Reproduce | New cluster/issuer, scoped identities and SQL mappings, local private DNS, approved image digest and compatible add-ons. | Never copy the old OIDC issuer or replace primary DNS by accident. |
| Reconcile passive | Regional-style Git path and namespace RBAC; zero worker replicas with no HPA/KEDA activation; verify render and live state. | Old app remains sole writer. No unverified new readiness probe authorizes traffic. |
| Validate | Read-only known-row, private image/TLS and configuration tests. Restore required CSI data only within its supported recovery target. | Classify missing configuration/data and repair before admission. |
| Handover | Stop producers, drain/record accepted work, fence old writers in live/Git/autoscalers, then activate new writers and test a synthetic transaction privately. | Only one writer set; never “rollback” by activating both. |
| Route | Change the authoritative application route only after business validation; observe accepted IDs, latency, errors and queue state. | Fence new writers before any return to old compute; retain a compatible shared DB schema. |
| Stabilize | Resume intended scaling/owners, verify backups and remove temporary exceptions; retain old compute until the approved rollback window expires. | If schema/data is incompatible, use the approved forward-recovery plan, not Kubernetes downgrade. |
| Retire | Prove no ingress, PVC, identity or queue consumers depend on old compute; remove only its resources and external assignments. | Do not delete shared ACR, DB, Service Bus or DNS. |

The new application can use the same managed database/queue when the ownership and connectivity
design allows it; region-loss promotion is not a prerequisite for replacing compute. A production
runbook requires executable resource-specific steps and approval. This worksheet is not live
replacement evidence.

</details>

## 4. Review and close

**Task:** have another reviewer challenge a proposed bad plan: “copy observed count into IaC,
reuse the old issuer, enable both workers and keep the old DB hostname after failback.”
Explain four independent failure modes and the evidence that would block each.

**Exit:** retain the classified diff, corrected desired projection and replacement decision table.
No cloud cleanup is needed for the offline path. Remove only named local fixture files after
retaining your evidence. Resume lab 22/23 at its recorded checkpoint, not at bootstrap.

References: [Bicep what-if](https://learn.microsoft.com/azure/azure-resource-manager/bicep/deploy-what-if),
[AKS baseline](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks),
[AKS upgrades](https://learn.microsoft.com/azure/aks/upgrade-cluster).

**Return:** [Lab index](../README.md#lab-index) or [regional preparation](../labs/23-multi-region.md).
