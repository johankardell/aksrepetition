# Lab 4 - Explain AKS capacity before adding nodes

**Level:** beginner. **Scenario:** a team asks for more nodes because its pods might not fit. You must distinguish a scheduling constraint, requested capacity, Azure quota and real workload demand.

**Outcome:** a capacity and placement worksheet for the existing pools, including availability, network and storage constraints.

## Lab contract

| Field | This lab |
|---|---|
| Entry | Lab 3 private API and identity checks passed. |
| Host and identity | Private build VM; operator's verified current-shell Bastion session, read-only Azure/Kubernetes inspection. |
| Time | Estimate: 45–60 minutes active; 0–10 minutes API/metrics wait. |
| Cost | No new resources or resizing; existing pools and foundation keep billing. |
| Changes | None to live resources; operator owns capacity, zone, address and storage worksheet. |
| Exit and re-entry | Retain actual requests/allocatable/quota and storage policies; refresh observations rather than resize to match an old worksheet. |

Use the [session, evidence and cost operating contract](../docs/operations.md) and [stage map](../docs/state.md). Times are planning estimates, not measured results.

## Prerequisites and resource boundary

Complete [lab 3](03-access-and-identity.md). Run Bash at the repository root on the private build VM with the active, verified native Bastion session. Reconnect and recheck context if returning.

This lab is **read-only** against existing resources. Do not resize pools, enable autoscalers, drain nodes, attach disks or alter networking. Six nodes and the rest of the foundation keep billing. Detailed autoscaling is [lab 15](15-scaling.md); actual upgrades are [lab 22](22-upgrades.md).

## Directives

### 1. Map Azure node pools to Kubernetes nodes

**Task:** identify which pool hosts platform components and where the orders workload will run. Compare desired Azure configuration with live node readiness.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
kubectl config current-context
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{name:name,mode:mode,count:count,sku:vmSize,os:osSKU,zones:availabilityZones,maxPods:maxPods,autoscaling:enableAutoScaling,taints:nodeTaints,surge:upgradeSettings.maxSurge}' -o json
kubectl --request-timeout=30s get nodes \
  -L agentpool,kubernetes.azure.com/mode,topology.kubernetes.io/zone,kubernetes.azure.com/os-sku
kubectl get nodes -o json | jq '.items[] | {
  name:.metadata.name,pool:.metadata.labels.agentpool,
  capacity:.status.capacity,allocatable:.status.allocatable,taints:.spec.taints}'
```

Expect `system` in System mode and `apps` in User mode, with three nodes each. The system pool's `CriticalAddonsOnly=true:NoSchedule` taint discourages ordinary workloads; orders explicitly select `agentpool: apps`.

An Azure pool's provisioning state is not Kubernetes node readiness. A node can exist while its kubelet, network or runtime is unhealthy. Investigate that layer before adding capacity.

Do not modify the underlying VM scale sets directly. Pool changes belong to the AKS API and must consider scheduling and disruption, not only the VM count.

</details>

### 2. Calculate requested capacity, not just VM size

**Task:** inspect `k8s/base/api.yaml` and `worker.yaml`. Calculate the baseline requests and explain why VM capacity is larger than workload-allocatable capacity.

<details>
<summary>Solution</summary>

The base requests two API replicas at 250m CPU/256Mi each and one worker at 100m/128Mi: **600m CPU and 640Mi memory total**, before rollout overlap, platform pods and DaemonSets. Requests drive scheduling; limits bound runtime usage. These are synthetic-workload values, not a customer sizing recommendation.

```bash
kubectl describe nodes -l agentpool=apps
kubectl get pods -n kube-system -o wide
az vm list-usage --location "$(lab_value Location)" -o table
```

Compare each node's Capacity, Allocatable and Allocated resources. Azure/OS/Kubernetes reservations reduce allocatable CPU and memory; DaemonSets consume some on every node. A sum across all nodes does not prove that a single large pod can fit on any one node.

The foundation has `maxSurge: 1` per pool. A one-node surge adds a VM while existing nodes are being replaced; reserve appropriate VM-family/regional quota, subnet/IP and disk capacity. Do not assume only steady-state capacity matters or that all operations always execute serially.

| Observation | Interpretation |
|---|---|
| `Insufficient cpu` with high requested CPU | Inspect pod requests and per-node allocatable space |
| Plenty of CPU but no matching node selector | Adding arbitrary nodes cannot satisfy the placement rule |
| High usage but pods are schedulable | Utilization is a tuning signal, not proof the cluster autoscaler will scale |
| Azure allocation/quota error | The pool cannot obtain requested VMs even if Kubernetes needs capacity |

If using `kubectl top`, remember it reports recent usage, not scheduler requests or historical capacity planning. Missing metrics are not zero usage; do not enable another monitoring stack just to fill this worksheet.

</details>

### 3. Check availability and address capacity

**Task:** distinguish multi-zone infrastructure, pod placement and application availability. Identify the node, pod and Service address spaces.

<details>
<summary>Solution</summary>

```bash
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{network:networkProfile,pools:agentPoolProfiles[].{name:name,maxPods:maxPods,subnet:vnetSubnetID,zones:availabilityZones}}' -o json
kubectl get nodes -o wide
kubectl get pdb -A
```

Azure CNI Overlay gives pods an overlay address space separate from node subnet IPs. Nodes still need subnet addresses and Azure capacity; services use their own virtual CIDR. Check actual `maxPods` and the supported overlay allocation model rather than applying a formula for a different Azure CNI mode.

Compare zones advertised on the pool with labels on actual nodes. In lab 5, compare pod placements too. The API's `ScheduleAnyway` topology spread is a **preference**, not a hard guarantee that each zone always has a replica.

The API PDB limits supported voluntary disruptions; it is not yet deployed here. It does not prevent node/zone failure and does not control Deployment rolling updates. Even multi-zone nodes plus a PDB do not prove business continuity if the queue, data store or application cannot recover.

</details>

### 4. Identify storage ownership before creating state

**Task:** inspect installed CSI drivers and storage classes. Explain which deletion can destroy data or leave Azure resources billing.

<details>
<summary>Solution</summary>

```bash
kubectl get csidrivers
kubectl get storageclass \
  -o custom-columns=NAME:.metadata.name,PROVISIONER:.provisioner,BINDING:.volumeBindingMode,RECLAIM:.reclaimPolicy
kubectl get pvc -A
kubectl get pv
```

The orders base uses bounded writable `emptyDir` mounts for temporary files, not durable PVCs. A restart/reschedule is not a backup strategy. No application volumes are created by this lab.

Azure Disk and Azure Files serve different attachment/sharing requirements. A bound volume can have zone, access-mode and per-node attachment constraints independent of CPU/memory. Inspect the actual class's binding and reclaim policies: deleting a PVC may delete backing storage, while retained disks and snapshots can survive and continue billing.

Do not create/delete a PVC to "prove" retention in this shared beginner baseline. The controlled durable-storage and restore exercises belong to [lab 21](21-data-recovery.md).

</details>

## Exit evidence

Record pool modes/counts, actual zones/readiness, SKU/OS, taints/selectors, allocatable/requested capacity, upgrade headroom, actual `maxPods` and CIDRs, and storage ownership. Leave pool size, version, networking and storage unchanged.

## Customer conversation

<details>
<summary>Model answer: Will adding a node fix every Pending pod?</summary>

No. Capacity helps only if the new node satisfies the pod's resource, affinity/selector, taint, zone and volume constraints and Azure can actually allocate it. Inspect scheduler evidence first.

</details>

<details>
<summary>Model answer: Why not schedule everything on the system pool to save money?</summary>

Separating application and system capacity reduces contention and clarifies lifecycle ownership. Rightsize the whole design against the workload and reliability requirements; do not remove placement controls just to make a lab fit.

</details>

## Cleanup and next step

There are no new resources or mutations to undo. Continue to [lab 5](05-acr-and-workloads.md), which supplies real image-pull, scheduling and application evidence. Disconnect the API tunnel if pausing; it does not deallocate Azure resources.

## References

[System node pools](https://learn.microsoft.com/azure/aks/use-system-pools), [resource management](https://learn.microsoft.com/azure/aks/developer-best-practices-resource-management), [Azure CNI Overlay](https://learn.microsoft.com/azure/aks/azure-cni-overlay), [storage best practices](https://learn.microsoft.com/azure/aks/operator-best-practices-storage), [AKS upgrade options](https://learn.microsoft.com/azure/aks/upgrade-cluster). Inspect the current service limits for your selected version and region rather than assuming all networking modes have the same capacity model.

---

**Next lab:** [Lab 5 - ACR and workloads](05-acr-and-workloads.md).
