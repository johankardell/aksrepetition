# Lab 12 - Measure RabbitMQ storage density with Azure Container Storage

**Level:** advanced. **Type:** optional and self-contained. **Scenario:** a messaging platform needs a three-member RabbitMQ cluster, but a managed disk for every stateful pod can exhaust the data-disk attachment limit of an AKS node long before CPU and memory are full.

**Outcome:** a measured comparison between ordinary Azure Disk CSI persistence and Azure Container Storage 2.x backed by local NVMe. Both deployments keep one PV and PVC per RabbitMQ member and spread the three members across three AKS nodes. The comparison distinguishes Kubernetes volume count, Azure managed-disk attachments, physical local devices, performance, and durability rather than claiming that a storage layer makes RabbitMQ stateless.

This lab creates a dedicated resource group and AKS cluster. It does not use or modify the cumulative labs 1-10 environment.

## Prerequisites, cost, and safety boundary

Use Azure CLI 2.83.0 or later, PowerShell 7.4+, `kubectl`, and an account that can create AKS resources and role assignments in a dedicated subscription or resource group. Install or update the `k8s-extension` CLI extension. Select a region supported by Azure Container Storage and a storage-optimized VM SKU with local NVMe capacity and sufficient quota.

Budget for two small system nodes and three storage-optimized user nodes for the duration of the exercise. `Standard_L8s_v3` is an example discovery candidate, not a promise of regional availability or the right production size. Use managed OS disks on a VM size that leaves local NVMe devices available to Azure Container Storage. The local-NVMe path is **ephemeral**: deleting, deallocating, reimaging, or replacing a node can destroy the data on that node.

Use only synthetic messages. Do not expose RabbitMQ publicly. Do not delete or deallocate a local-NVMe node during the failure exercise. The cluster, Azure Container Storage extension, disks, and any optional Elastic SAN resources are billable until removed.

Create an ignored disposable working folder for rendered manifests and evidence:

```powershell
$Work = Join-Path $env:TEMP 'aks-rabbitmq-storage-lab'
New-Item -ItemType Directory -Path $Work -Force | Out-Null
```

## 1. Define what “fewer disks” must prove

**Task:** write a short measurement plan before deployment. Identify at least four distinct quantities that could be called a “disk,” state which AKS or Azure limit is under test, and predict what should and should not change when the RabbitMQ cluster moves from Azure Disk CSI to Azure Container Storage local NVMe.

Include a durability hypothesis. Explain what a three-member quorum queue can survive, what local NVMe changes, and why application replication does not turn an ephemeral device into durable storage.

<details>
<summary>Solution</summary>

Measure these separately:

| Quantity | Azure Disk CSI expectation | Azure Container Storage local NVMe expectation |
|---|---:|---:|
| RabbitMQ members | 3 | 3 |
| Kubernetes PVCs/PVs | 3/3 | 3/3 |
| Azure managed data-disk resources created for the workload | 3 | 0 |
| Managed data-disk attachments consumed on AKS VMs | 3 total | 0 for RabbitMQ data |
| Local NVMe devices physically present | SKU-dependent | SKU-dependent; discovered from the VM SKU |

The bottleneck under test is the maximum number of Azure managed data disks attachable to each VM size and the associated attach/detach control-plane operations. Azure Container Storage does **not** let three RabbitMQ members share one writable filesystem and does not reduce the three logical PVCs. Local NVMe reuses devices already present inside each storage-optimized VM, so it avoids creating and attaching one Azure managed disk per claim.

A three-member RabbitMQ quorum queue remains available after one member fails as long as the other two members are healthy and connected. With local NVMe, loss of one node also loses that member's local replica. The remaining quorum can rebuild a replacement member only while a majority and a valid copy remain. Losing two members, correlated node replacement, a bad operational sequence, or failure before a replica catches up can lose availability or data. Local NVMe therefore suits explicitly replicated workloads with a tested recovery model; it is not equivalent to durable remote storage.

For a durable backend that also avoids one Azure managed-disk attachment per PVC, Azure Container Storage 2.x can use Elastic SAN. That consolidates capacity and supports high volume density, but it has different cost, regional, networking, performance, and failure-domain characteristics. It is evaluated as a design extension later rather than silently substituted for local NVMe.

</details>

## 2. Discover capacity and create the isolated AKS cluster

**Task:** discover a supported AKS version, an ordinary system-pool SKU, a local-NVMe user-pool SKU, regional Azure Container Storage availability, and quota. Create a dedicated cluster with two system nodes and exactly three storage nodes. Make the storage pool a user pool and keep ordinary system components off it where practical.

Record the selected SKU's local disk count, local capacity, maximum data-disk attachments, zone support, and restrictions. Do not continue if the SKU exposes no usable local NVMe data disk.

<details>
<summary>Solution</summary>

Set unique values and verify the active subscription before creating anything:

```powershell
$SubscriptionId = az account show --query id -o tsv
$Location = 'swedencentral'
$ResourceGroup = 'rg-aks-rabbitmq-storage-lab'
$ClusterName = 'aks-rabbitmq-storage-lab'
$SystemVmSize = 'Standard_D4ds_v5'
$StorageVmSize = 'Standard_L8s_v3'

az account show --query '{subscription:id,name:name,tenant:tenantId}' -o table
az --version
az extension add --upgrade --name k8s-extension
az aks get-versions --location $Location -o table
az vm list-usage --location $Location -o table
az vm list-skus --location $Location --size $StorageVmSize --all `
  --query "[].{name:name,zones:locationInfo[0].zones,capabilities:capabilities,restrictions:restrictions}" -o json
```

In the SKU output, inspect `MaxDataDiskCount`, `MaxResourceVolumeMB`, `vCPUs`, restrictions, and zones. Confirm current Azure Container Storage regional availability in the reference linked at the end of the lab. A size with one local NVMe device can lose that device to an ephemeral OS disk; this lab explicitly requests managed OS disks.

Create the resource group, a small system pool, and a three-node storage pool:

```powershell
az group create --name $ResourceGroup --location $Location `
  --tags purpose=aks-rabbitmq-storage-lab owner=$env:USERNAME

az aks create --resource-group $ResourceGroup --name $ClusterName `
  --location $Location --nodepool-name system `
  --node-count 2 --node-vm-size $SystemVmSize `
  --node-osdisk-type Managed --generate-ssh-keys

az aks nodepool add --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name storage --mode User --node-count 3 --node-vm-size $StorageVmSize `
  --node-osdisk-type Managed --labels workload=messaging storage=local-nvme

az aks get-credentials --resource-group $ResourceGroup --name $ClusterName --overwrite-existing
kubectl get nodes -L agentpool,kubernetes.azure.com/mode,workload,storage,topology.kubernetes.io/zone
```

Expected: two Ready `system` nodes and three Ready `storage` nodes. If three zones are available and required for the exercise, recreate the storage pool with an approved zonal design before deploying RabbitMQ. Do not infer zone resilience merely from a region supporting zones.

</details>

## 3. Install a pinned RabbitMQ Cluster Operator

**Task:** inspect the current RabbitMQ Cluster Operator release, review its manifest, pin the selected release for reproducibility, and install it. Confirm the CRD and controller are ready before creating a RabbitMQ resource.

Do not copy a floating third-party manifest into a production delivery path. For restricted environments, mirror and scan the operator and RabbitMQ images in an approved registry.

<details>
<summary>Solution</summary>

Discover the current release, record it, and download that exact version:

```powershell
$OperatorVersion = gh release view --repo rabbitmq/cluster-operator --json tagName -q .tagName
$OperatorManifest = Join-Path $Work "cluster-operator-$OperatorVersion.yaml"
$OperatorUri = "https://github.com/rabbitmq/cluster-operator/releases/download/$OperatorVersion/cluster-operator.yml"
Invoke-WebRequest -Uri $OperatorUri -OutFile $OperatorManifest

Select-String -Path $OperatorManifest -Pattern 'image:|kind: CustomResourceDefinition|kind: ClusterRole'
# Review the complete manifest and image references before applying.
kubectl apply -f $OperatorManifest
kubectl rollout status deployment/rabbitmq-cluster-operator -n rabbitmq-system --timeout=300s
kubectl get crd rabbitmqclusters.rabbitmq.com
kubectl get pods -n rabbitmq-system -o wide
```

The controller must be Available and the `rabbitmqclusters.rabbitmq.com` CRD must exist. Save the selected release and image digest in the evidence folder. A production path should use a reviewed manifest or package in source control and immutable image references.

</details>

## 4. Establish the Azure Disk CSI baseline

**Task:** deploy a three-member RabbitMQ cluster using the built-in managed Premium SSD storage class. Require one RabbitMQ member per storage node, make quorum queues the default, and keep the service private. Prove cluster health, per-node placement, one PVC per member, and one Azure managed disk per PVC.

Create a synthetic durable quorum queue and publish test messages before collecting evidence.

<details>
<summary>Solution</summary>

Create the namespace and baseline manifest:

```powershell
$BaselineManifest = Join-Path $Work 'rabbitmq-managed-disks.yaml'
@'
apiVersion: v1
kind: Namespace
metadata:
  name: rabbitmq
---
apiVersion: rabbitmq.com/v1beta1
kind: RabbitmqCluster
metadata:
  name: rabbit-disk
  namespace: rabbitmq
spec:
  replicas: 3
  persistence:
    storageClassName: managed-csi-premium
    storage: 16Gi
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: "1"
      memory: 1Gi
  rabbitmq:
    additionalConfig: |
      default_queue_type = quorum
      cluster_partition_handling = pause_minority
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: agentpool
            operator: In
            values:
            - storage
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: app.kubernetes.io/name
            operator: In
            values:
            - rabbit-disk
        topologyKey: kubernetes.io/hostname
'@ | Set-Content -Path $BaselineManifest -Encoding utf8

kubectl apply -f $BaselineManifest
kubectl wait rabbitmqcluster/rabbit-disk -n rabbitmq `
  --for=condition=AllReplicasReady --timeout=600s
kubectl get pods -n rabbitmq -l app.kubernetes.io/name=rabbit-disk `
  -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName
kubectl get nodes -l agentpool=storage -L topology.kubernetes.io/zone
kubectl get pvc,pv -n rabbitmq
kubectl exec -n rabbitmq rabbit-disk-server-0 -- rabbitmq-diagnostics cluster_status
$BaselinePvcs = @(kubectl get pvc -n rabbitmq `
  -l app.kubernetes.io/name=rabbit-disk -o jsonpath='{range .items[*]}{.metadata.name}{"`n"}{end}')
```

Expected: three server pods on three different `storage` nodes, three Bound PVCs, three PVs using the Azure Disk CSI provisioner, and all three RabbitMQ members in cluster status.

Create a quorum queue through the local management CLI. Read credentials from the generated Secret without printing them:

```powershell
function ConvertFrom-KubeSecret([string]$Value) {
  [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
}

$RabbitUser = ConvertFrom-KubeSecret (kubectl get secret rabbit-disk-default-user -n rabbitmq -o jsonpath='{.data.username}')
$RabbitPassword = ConvertFrom-KubeSecret (kubectl get secret rabbit-disk-default-user -n rabbitmq -o jsonpath='{.data.password}')

kubectl exec -n rabbitmq rabbit-disk-server-0 -- rabbitmqadmin `
  --username $RabbitUser --password $RabbitPassword `
  declare queue name=lab-quorum durable=true arguments='{"x-queue-type":"quorum"}'
kubectl exec -n rabbitmq rabbit-disk-server-0 -- rabbitmqctl `
  list_queues name type leader members_online messages
```

If the installed `rabbitmqadmin` syntax differs, use `rabbitmqadmin --help` from the pod and supply the same queue properties. Do not place credentials in a committed manifest.

Measure Azure managed disks in the AKS node resource group:

```powershell
$NodeResourceGroup = az aks show -g $ResourceGroup -n $ClusterName --query nodeResourceGroup -o tsv
az disk list -g $NodeResourceGroup `
  --query "[?starts_with(name, 'pvc-')].{name:name,sizeGiB:diskSizeGb,managedBy:managedBy,sku:sku.name}" -o table
$BaselineDiskCount = [int](az disk list -g $NodeResourceGroup `
  --query "length([?starts_with(name, 'pvc-')])" -o tsv)
"RabbitMQ Azure managed disks: $BaselineDiskCount"
```

The baseline count must be three. If unrelated PVC disks exist, identify the three PV volume handles and measure those exact disk resource IDs instead of relying on the name prefix.

Clear local credential variables:

```powershell
$RabbitUser = $null
$RabbitPassword = $null
```

</details>

## 5. Remove the baseline without hiding retained disks

**Task:** delete only the baseline RabbitMQ resource and its claims. Verify the reclaim policy actually removes the three workload disks before enabling the comparison backend. Keep the operator and AKS nodes.

<details>
<summary>Solution</summary>

Delete the custom resource, wait for its pods to disappear, and inspect PVC/PV cleanup:

```powershell
kubectl delete rabbitmqcluster rabbit-disk -n rabbitmq --wait=true
foreach ($Claim in $BaselinePvcs) {
  if ($Claim) { kubectl delete pvc $Claim -n rabbitmq --wait=true }
}
kubectl get pv
```

StatefulSet claims can outlive their pods by design, which is why the three captured names are deleted explicitly. If any other claim remains, inspect its owner references and reclaim policy; do not delete unrelated PVs. Wait for Azure resource deletion:

```powershell
$deadline = (Get-Date).AddMinutes(10)
do {
  $Remaining = [int](az disk list -g $NodeResourceGroup `
    --query "length([?starts_with(name, 'pvc-')])" -o tsv)
  if ($Remaining -eq 0) { break }
  Start-Sleep -Seconds 15
} while ((Get-Date) -lt $deadline)

if ($Remaining -ne 0) {
  throw "Managed PVC disks remain. Inspect them before continuing."
}
```

This prevents a false comparison in which old managed disks are mistaken for Azure Container Storage allocations.

</details>

## 6. Enable Azure Container Storage and redeploy RabbitMQ on local NVMe

**Task:** enable Azure Container Storage 2.x with the local-NVMe storage type. Verify the installer, CSI driver, storage class, and per-node capacity. Redeploy the same three-member RabbitMQ topology using that class and the explicit ephemeral-storage acknowledgement required for PVC compatibility.

Prove that the RabbitMQ and Kubernetes object counts remain unchanged while Azure managed-disk resources and attachments do not return.

<details>
<summary>Solution</summary>

Enable Azure Container Storage and verify its components:

```powershell
az aks update --resource-group $ResourceGroup --name $ClusterName `
  --enable-azure-container-storage ephemeralDisk

kubectl get deployments,pods -n kube-system | Select-String acstor
kubectl get storageclass local-csi
kubectl get csistoragecapacities.storage.k8s.io -n kube-system `
  -o custom-columns=NAME:.metadata.name,CLASS:.storageClassName,CAPACITY:.capacity,NODE:.nodeTopology.matchLabels.'topology\.localdisk\.csi\.acstor\.io/node'
```

Create the local-NVMe RabbitMQ resource. The operator does not expose PVC-template annotations directly, so use its StatefulSet override to add the Azure Container Storage acknowledgement to the generated claim template:

```powershell
$LocalManifest = Join-Path $Work 'rabbitmq-local-nvme.yaml'
@'
apiVersion: rabbitmq.com/v1beta1
kind: RabbitmqCluster
metadata:
  name: rabbit-local
  namespace: rabbitmq
spec:
  replicas: 3
  persistence:
    storageClassName: local-csi
    storage: 16Gi
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: "1"
      memory: 1Gi
  rabbitmq:
    additionalConfig: |
      default_queue_type = quorum
      cluster_partition_handling = pause_minority
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: agentpool
            operator: In
            values:
            - storage
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: app.kubernetes.io/name
            operator: In
            values:
            - rabbit-local
        topologyKey: kubernetes.io/hostname
  override:
    statefulSet:
      spec:
        volumeClaimTemplates:
        - apiVersion: v1
          kind: PersistentVolumeClaim
          metadata:
            name: persistence
            namespace: rabbitmq
            annotations:
              localdisk.csi.acstor.io/accept-ephemeral-storage: "true"
          spec:
            accessModes:
            - ReadWriteOnce
            resources:
              requests:
                storage: 16Gi
            storageClassName: local-csi
            volumeMode: Filesystem
'@ | Set-Content -Path $LocalManifest -Encoding utf8

kubectl apply -f $LocalManifest
kubectl wait rabbitmqcluster/rabbit-local -n rabbitmq `
  --for=condition=AllReplicasReady --timeout=600s
kubectl get pods -n rabbitmq -l app.kubernetes.io/name=rabbit-local `
  -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName
kubectl get pvc -n rabbitmq -o custom-columns=NAME:.metadata.name,CLASS:.spec.storageClassName,PV:.spec.volumeName,STATUS:.status.phase
kubectl get pv -o custom-columns=NAME:.metadata.name,DRIVER:.spec.csi.driver,NODE_AFFINITY:.spec.nodeAffinity.required.nodeSelectorTerms
kubectl exec -n rabbitmq rabbit-local-server-0 -- rabbitmq-diagnostics cluster_status
```

Expected: three pods on three distinct storage nodes, three Bound PVCs, three PVs using `localdisk.csi.acstor.io`, and a healthy three-member cluster.

Repeat the queue creation from task 4 against `rabbit-local`, then measure Azure disks:

```powershell
$RabbitUser = ConvertFrom-KubeSecret (kubectl get secret rabbit-local-default-user -n rabbitmq -o jsonpath='{.data.username}')
$RabbitPassword = ConvertFrom-KubeSecret (kubectl get secret rabbit-local-default-user -n rabbitmq -o jsonpath='{.data.password}')
kubectl exec -n rabbitmq rabbit-local-server-0 -- rabbitmqadmin `
  --username $RabbitUser --password $RabbitPassword `
  declare queue name=lab-quorum durable=true arguments='{"x-queue-type":"quorum"}'
kubectl exec -n rabbitmq rabbit-local-server-0 -- rabbitmqctl `
  list_queues name type leader members_online messages

$LocalDiskCount = [int](az disk list -g $NodeResourceGroup `
  --query "length([?starts_with(name, 'pvc-')])" -o tsv)
"RabbitMQ Azure managed disks: $LocalDiskCount"
$RabbitUser = $null
$RabbitPassword = $null
```

The expected workload managed-disk count is zero. The result is **three PVCs backed by node-local storage, not one shared disk**. The three storage VMs still have physical local NVMe devices, and their managed OS disks are unrelated to RabbitMQ PVC attachment density.

If the operator rejects the overridden claim template because its schema changed, inspect the installed CRD with `kubectl explain rabbitmqcluster.spec.override.statefulset` and the generated StatefulSet. Do not remove the ephemeral-storage acknowledgement. Use the current operator-supported override shape or a reviewed mutating policy to place that annotation on each generated claim.

</details>

## 7. Test placement and the local-storage failure boundary

**Task:** select one RabbitMQ member, cordon its node, delete only that pod, and observe the result. Confirm the remaining two members retain quorum. Explain why the deleted pod cannot simply move with its existing local PV and why uncordoning the original node is recovery, not failover to durable storage.

Restore all three members before continuing. Do not drain, reimage, scale down, delete, or deallocate the node.

<details>
<summary>Solution</summary>

Capture the selected pod, node, and PV before the test:

```powershell
$TestPod = 'rabbit-local-server-2'
$TestNode = kubectl get pod $TestPod -n rabbitmq -o jsonpath='{.spec.nodeName}'
kubectl get pod $TestPod -n rabbitmq -o wide
kubectl get pvc -n rabbitmq
kubectl cordon $TestNode
kubectl delete pod $TestPod -n rabbitmq
Start-Sleep -Seconds 20
kubectl get pods -n rabbitmq -o wide
kubectl get events -n rabbitmq --sort-by=.lastTimestamp | Select-Object -Last 30
kubectl exec -n rabbitmq rabbit-local-server-0 -- rabbitmq-diagnostics cluster_status
```

Expected: the replacement ordinal is Pending because its existing local PV has node affinity for the cordoned node. The other two members remain a majority. The scheduler cannot move that PV's bytes to another node; `ReadWriteOnce` and local topology are constraints, not replication.

Recover by making the original node schedulable:

```powershell
kubectl uncordon $TestNode
kubectl wait pod/$TestPod -n rabbitmq --for=condition=Ready --timeout=600s
kubectl exec -n rabbitmq rabbit-local-server-0 -- rabbitmq-diagnostics cluster_status
kubectl exec -n rabbitmq rabbit-local-server-0 -- rabbitmqctl `
  list_queues name type leader members_online messages
```

All three members and all quorum-queue replicas must return. If the node itself was lost, this procedure would not restore its local data; RabbitMQ-specific member replacement and replica-repair procedures would be required while a majority still exists.

</details>

## 8. Make the production recommendation

**Task:** produce a short decision record for the customer. Compare Azure Disk CSI, Azure Container Storage local NVMe, and Azure Container Storage with Elastic SAN. Include attachment density, latency, durability, failure domain, minimum cost shape, snapshots, operational recovery, and which evidence from this lab is insufficient for a production decision.

<details>
<summary>Solution</summary>

| Backend | Attachment-density effect | Durability and recovery | Best fit / caution |
|---|---|---|---|
| Azure Disk CSI | One managed disk and attachment per RabbitMQ PVC | Durable remote disk; zone and redundancy behavior depends on class/SKU. A disk normally follows a rescheduled pod subject to topology. | Straightforward and isolated performance, but per-VM disk count and attach/detach rate can become density bottlenecks. |
| Azure Container Storage local NVMe | Zero Azure managed data-disk attachments for the PVCs; volumes consume local capacity already in each VM | Ephemeral on node loss; no storage-layer replication or snapshots. RabbitMQ replication is the only copy across nodes. | Highest local performance and high density for explicitly replicated, failure-tested workloads. Requires strict disruption and replacement procedures. |
| Azure Container Storage + Elastic SAN | Avoids per-PVC Azure managed-disk attachments and supports thousands of volumes per cluster | Durable shared block storage with LRS/ZRS options and snapshots; network and SAN failure domains must be designed. | Messaging and other durable high-density workloads, subject to regional availability, minimum provisioned capacity, networking, throughput pool, and cost. |

The lab proves object counts, node separation, cluster health, a one-member scheduling failure, and managed-disk resource counts. It does not prove production throughput, tail latency, recovery time after permanent node loss, zone failure behavior, safe rolling upgrades, backup/restore, queue rebalance, publisher confirms, consumer acknowledgements, encryption requirements, or total cost at expected scale. Benchmark and failure-test the chosen backend with representative queue topology and message sizes.

For an optional Elastic SAN validation, register `Microsoft.ElasticSan`, assign the kubelet identity the narrowly scoped **Azure Container Storage Operator** role, enable `elasticSan`, and deploy the same RabbitMQ resource with `azuresan-csi`. Review the default 1-TiB initial capacity and network access before applying it. Do not run that extension merely to complete this lab if the cost and region have not been approved.

</details>

## Exit evidence

Retain:

- selected AKS/Kubernetes, operator, RabbitMQ, Azure Container Storage, VM SKU, and node image versions;
- three-node placement for both RabbitMQ deployments;
- three PVC/PV records for each backend;
- baseline count of three RabbitMQ Azure managed disks and local-NVMe count of zero;
- local CSI capacity per storage node;
- healthy quorum queue membership before and after the controlled cordon;
- the decision record and explicit durability caveat.

The lab is incomplete if it reports only `kubectl get pvc`, calls local NVMe “persistent” without its loss boundary, or claims that three RabbitMQ members now use one disk.

## Customer conversation

<details>
<summary>Model answer: Does Azure Container Storage remove the need for a volume per RabbitMQ pod?</summary>

No. Each RabbitMQ member still owns a distinct data directory, PVC, and PV. Azure Container Storage changes how those volumes are provisioned and attached. Local NVMe avoids Azure managed-disk attachments; Elastic SAN consolidates durable capacity. Neither turns three stateful members into one shared-writer filesystem.

</details>

<details>
<summary>Model answer: Is a quorum queue enough to make local NVMe safe?</summary>

It can tolerate a single member loss while a majority remains, but it is not a backup and does not eliminate correlated failures. Node replacement destroys that node's local copy. Operational sequencing, replica health, failure domains, publisher confirms, tested member replacement, and backup or upstream replay strategy still matter.

</details>

<details>
<summary>Model answer: Why not pack all three RabbitMQ pods onto one large NVMe node?</summary>

That would put all members and all local replicas in one VM failure domain, defeating the purpose of a three-member cluster. Required pod anti-affinity protects node separation; zone spread should be added when the selected regional and storage design supports it.

</details>

<details>
<summary>Model answer: Does zero managed data disks mean zero storage cost?</summary>

No. Storage-optimized VMs include local capacity in the VM price and may be more expensive than general-purpose nodes. Elastic SAN has provisioned capacity and performance charges. Managed OS disks, backups, monitoring, and idle nodes also cost money.

</details>

## Cleanup

Delete the RabbitMQ resource and namespace first, verify claims are gone, then delete the dedicated resource group. If you created Elastic SAN resources, inspect their volumes, snapshots, private endpoints, and retention requirements before deletion.

<details>
<summary>Solution: complete teardown</summary>

```powershell
kubectl delete rabbitmqcluster rabbit-local -n rabbitmq --ignore-not-found --wait=true
kubectl delete namespace rabbitmq --ignore-not-found --wait=true
kubectl get pv
az resource list --resource-group $ResourceGroup -o table
```

Review the inventory. Only after confirming that the group is the dedicated disposable lab boundary:

```powershell
az group delete --name $ResourceGroup
Remove-Item -Path $Work -Recurse -Force
```

The Azure command prompts for confirmation. After deletion completes, check Cost Management and confirm no Elastic SAN, snapshot, disk, private endpoint, or role assignment created outside the resource group remains.

</details>

## References and support

Sources reviewed 2026-09-23:

- [Install Azure Container Storage with AKS](https://learn.microsoft.com/azure/storage/container-storage/install-container-storage-aks)
- [Azure Container Storage overview and backend comparison](https://learn.microsoft.com/azure/storage/container-storage/container-storage-introduction)
- [Use Azure Container Storage with local NVMe](https://learn.microsoft.com/azure/storage/container-storage/use-container-storage-with-local-disk)
- [Use Azure Container Storage with Elastic SAN](https://learn.microsoft.com/azure/storage/container-storage/use-container-storage-with-elastic-san)
- [AKS ephemeral NVMe best practices](https://learn.microsoft.com/azure/aks/best-practices-storage-nvme)
- [Azure VM disk scalability targets](https://learn.microsoft.com/azure/virtual-machines/disks-scalability-targets)
- [RabbitMQ Cluster Operator installation](https://www.rabbitmq.com/kubernetes/operator/install-operator)
- [RabbitMQ Cluster Operator configuration](https://www.rabbitmq.com/kubernetes/operator/using-operator)
- [RabbitMQ quorum queues](https://www.rabbitmq.com/docs/quorum-queues)
- [Kubernetes persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Kubernetes pod anti-affinity and node affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)
