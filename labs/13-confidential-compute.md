# Lab 13 - Isolate confidential and ordinary workloads on one AKS cluster

**Level:** advanced. **Type:** optional and self-contained. **Scenario:** a customer needs hardware-protected memory for a small set of sensitive services but does not want ordinary workloads consuming more expensive confidential-compute capacity.

**Outcome:** one AKS cluster with ordinary and AMD SEV-SNP Confidential VM (CVM) user pools, a mixed workload, and evidence that scheduling controls deliberately steer sensitive pods to CVMs and ordinary pods away from them. The lab separates a node-pool trust boundary from confidential containers and shows how to scale confidential capacity down when it is not required.

This lab creates a dedicated resource group and cluster. It does not use or modify the cumulative labs 1-10 environment.

## Prerequisites, cost, and scope

Use a dedicated subscription or resource group, Azure CLI, PowerShell 7.4+, and `kubectl`. The operator needs permission to create AKS clusters and node pools. Select a region where a supported AMD confidential VM size such as `Standard_DC4as_v5` is available to the subscription with enough family quota.

Budget for two system nodes, one ordinary user node, and one confidential user node during the active exercise. Confidential VM availability and price vary by region and agreement. The lab uses Linux; Windows, Intel TDX, FIPS, ARM64, Trusted Launch, pod sandboxing, confidential containers, and node auto-provisioning are not combined with the CVM pool.

Use only synthetic data. A CVM node pool protects VM memory and state from the hypervisor and host management code, but **all pods on a CVM node share that node-level trust boundary**. This exercise does not claim per-pod isolation from the node administrator, validate application attestation, or establish regulatory compliance.

Create an ignored disposable folder for manifests and evidence:

```powershell
$Work = Join-Path $env:TEMP 'aks-confidential-compute-lab'
New-Item -ItemType Directory -Path $Work -Force | Out-Null
```

## 1. Choose the confidential-computing boundary

**Task:** compare AKS Confidential VM node pools with AKS confidential containers. Decide which model matches a workload that should migrate without code changes, then document the trust boundary, current support state, incompatible features, and what evidence would be needed beyond successful scheduling.

<details>
<summary>Solution</summary>

Use an AKS CVM node pool for this lab. A supported AMD SEV-SNP VM size protects the whole worker VM's memory and state from the hypervisor and host management code. Existing Linux containers can run without adopting a special runtime or per-pod security policy. Every pod on that worker is inside the same VM-level trust boundary, so Kubernetes placement and node administration remain important.

AKS confidential containers use Kata Confidential Containers to provide a stronger per-pod utility-VM boundary and attestation/policy model. As of the source review date, that feature is preview, uses the `KataCcIsolation` workload runtime, has a separate limitation set, and cannot share a node pool with CVMs. It should be evaluated separately rather than added to this CVM exercise.

Successful placement proves only that the scheduler used the intended node pool. A production acceptance plan also needs:

- verification of supported CVM SKU and CVM node image;
- an attestation and secure key-release design if secrets depend on measured platform state;
- application, image, supply-chain, identity, network, storage, logging, and backup controls;
- administrator and incident-response boundaries;
- representative performance and cost testing;
- upgrade, scale, node-replacement, and regional-availability testing;
- documented compliance interpretation by the responsible governance team.

</details>

## 2. Discover regional support and create ordinary capacity

**Task:** discover a supported AKS version, ordinary VM sizes, confidential VM sizes, regional restrictions, and quota. Create a cluster with a two-node system pool, then add a one-node ordinary user pool for application workloads. Keep application placement explicit rather than relying on the scheduler to use the system pool.

<details>
<summary>Solution</summary>

Set unique values and verify subscription context:

```powershell
$SubscriptionId = az account show --query id -o tsv
$Location = 'swedencentral'
$ResourceGroup = 'rg-aks-confidential-lab'
$ClusterName = 'aks-confidential-lab'
$SystemVmSize = 'Standard_D4ds_v5'
$OrdinaryVmSize = 'Standard_D4ds_v5'
$ConfidentialVmSize = 'Standard_DC4as_v5'

az account show --query '{subscription:id,name:name,tenant:tenantId}' -o table
az aks get-versions --location $Location -o table
az vm list-usage --location $Location -o table
az vm list-skus --location $Location --size $ConfidentialVmSize --all `
  --query "[].{name:name,zones:locationInfo[0].zones,restrictions:restrictions,capabilities:capabilities}" -o json
```

Confirm the size is an AKS-supported AMD confidential VM SKU and has no subscription or regional restriction. Intel TDX confidential VMs are not currently supported for AKS node pools.

Create the cluster and ordinary user pool:

```powershell
az group create --name $ResourceGroup --location $Location `
  --tags purpose=aks-confidential-compute-lab owner=$env:USERNAME

az aks create --resource-group $ResourceGroup --name $ClusterName `
  --location $Location --nodepool-name system `
  --node-count 2 --node-vm-size $SystemVmSize `
  --node-taints CriticalAddonsOnly=true:NoSchedule `
  --generate-ssh-keys

az aks nodepool add --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name ordinary --mode User --node-count 1 --node-vm-size $OrdinaryVmSize `
  --labels workload-tier=ordinary

az aks get-credentials --resource-group $ResourceGroup --name $ClusterName --overwrite-existing
kubectl get nodes -L agentpool,kubernetes.azure.com/mode,workload-tier
```

Expected: two tainted system nodes and one ordinary user node. The system taint prevents application pods without a matching toleration from consuming the system pool.

</details>

## 3. Add and verify the confidential VM pool

**Task:** add one Linux CVM user node with a label that workloads can select and a `NoSchedule` taint that ordinary workloads do not tolerate. Configure cluster autoscaling with a minimum of zero and a small approved maximum so confidential capacity can disappear when no sensitive workload needs it.

Verify both the VM SKU and the CVM-specific node image. Do not treat a custom label alone as proof of confidential hardware.

<details>
<summary>Solution</summary>

Add the CVM pool:

```powershell
az aks nodepool add --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --mode User --node-count 1 --node-vm-size $ConfidentialVmSize `
  --os-type Linux --os-sku AzureLinux `
  --labels workload-tier=confidential `
  --node-taints workload-tier=confidential:NoSchedule `
  --enable-cluster-autoscaler --min-count 0 --max-count 2
```

Verify the Azure configuration:

```powershell
az aks nodepool show -g $ResourceGroup --cluster-name $ClusterName -n cvm `
  --query '{vmSize:vmSize,osType:osType,osSku:osSKU,nodeImageVersion:nodeImageVersion,count:count,min:minCount,max:maxCount,autoscaling:enableAutoScaling,taints:nodeTaints,labels:nodeLabels}' -o json
```

The `vmSize` must be the selected supported confidential VM size. The `nodeImageVersion` must identify a CVM image, for example by containing `CVMcontainerd`; exact OS and image versions depend on the selected Kubernetes version.

Verify Kubernetes-visible placement metadata:

```powershell
kubectl get nodes `
  -L agentpool,workload-tier,kubernetes.azure.com/mode,kubernetes.azure.com/os-sku
kubectl get nodes -l agentpool=cvm -o jsonpath='{range .items[*]}{.metadata.name}{" taints="}{.spec.taints}{"`n"}{end}'
```

The CVM node must have `workload-tier=confidential:NoSchedule`. The label is a scheduling input, not independent hardware attestation; the Azure node-pool SKU and image evidence establish the configured platform type.

</details>

## 4. Deploy a mixed workload with hard placement boundaries

**Task:** create one ordinary deployment and one confidential deployment using the same simple container image. The ordinary workload must run only on the ordinary user pool. The confidential workload must both tolerate the CVM taint and require the CVM pool.

Prove the resulting placement from pod, node, and pool evidence. Explain why the toleration and selector serve different purposes.

<details>
<summary>Solution</summary>

Create the namespace and both deployments:

```powershell
$MixedManifest = Join-Path $Work 'mixed-workloads.yaml'
@'
apiVersion: v1
kind: Namespace
metadata:
  name: mixed-workloads
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ordinary-api
  namespace: mixed-workloads
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ordinary-api
  template:
    metadata:
      labels:
        app: ordinary-api
        data-classification: public
    spec:
      nodeSelector:
        agentpool: ordinary
      containers:
      - name: web
        image: mcr.microsoft.com/azurelinux/busybox:1.36
        command: ["/bin/sh", "-c"]
        args:
        - while true; do printf 'ordinary workload\n' | nc -l -p 8080; done
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: 25m
            memory: 32Mi
          limits:
            cpu: 100m
            memory: 64Mi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: confidential-api
  namespace: mixed-workloads
spec:
  replicas: 1
  selector:
    matchLabels:
      app: confidential-api
  template:
    metadata:
      labels:
        app: confidential-api
        data-classification: confidential
    spec:
      nodeSelector:
        agentpool: cvm
      tolerations:
      - key: workload-tier
        operator: Equal
        value: confidential
        effect: NoSchedule
      containers:
      - name: web
        image: mcr.microsoft.com/azurelinux/busybox:1.36
        command: ["/bin/sh", "-c"]
        args:
        - while true; do printf 'confidential workload\n' | nc -l -p 8080; done
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: 25m
            memory: 32Mi
          limits:
            cpu: 100m
            memory: 64Mi
'@ | Set-Content -Path $MixedManifest -Encoding utf8

kubectl apply -f $MixedManifest
kubectl rollout status deployment/ordinary-api -n mixed-workloads --timeout=300s
kubectl rollout status deployment/confidential-api -n mixed-workloads --timeout=300s
kubectl get pods -n mixed-workloads -o wide
kubectl get pods -n mixed-workloads `
  -o custom-columns=POD:.metadata.name,CLASS:.metadata.labels.data-classification,NODE:.spec.nodeName
kubectl get nodes -L agentpool,workload-tier
```

Expected: both ordinary replicas run on the `ordinary` pool and the confidential replica runs on the `cvm` pool.

The taint repels pods that do not explicitly tolerate confidential capacity. A toleration only removes that rejection; it does not attract a pod to the CVM pool. The `agentpool: cvm` selector attracts and requires the sensitive workload to run there. The ordinary selector is an explicit cost and trust-boundary control that prevents ordinary pods from drifting to system or confidential nodes.

In a shared platform, prefer policy-controlled labels and admission rules based on a workload classification contract rather than relying on every application author to copy YAML correctly.

</details>

## 5. Prove that a toleration is not placement

**Task:** create a temporary pod that tolerates the confidential taint but has no selector or required affinity. Observe where it runs and explain why its location is not guaranteed. Then remove it.

<details>
<summary>Solution</summary>

Create the temporary pod:

```powershell
$TolerationOnlyManifest = Join-Path $Work 'toleration-only.yaml'
@'
apiVersion: v1
kind: Pod
metadata:
  name: toleration-only
  namespace: mixed-workloads
spec:
  tolerations:
  - key: workload-tier
    operator: Equal
    value: confidential
    effect: NoSchedule
  containers:
  - name: sleeper
    image: mcr.microsoft.com/azurelinux/busybox:1.36
    command: ["/bin/sh", "-c", "sleep 3600"]
    resources:
      requests:
        cpu: 10m
        memory: 16Mi
      limits:
        cpu: 50m
        memory: 32Mi
'@ | Set-Content -Path $TolerationOnlyManifest -Encoding utf8

kubectl apply -f $TolerationOnlyManifest
kubectl wait pod/toleration-only -n mixed-workloads --for=condition=Ready --timeout=180s
kubectl get pod toleration-only -n mixed-workloads -o wide
kubectl delete pod toleration-only -n mixed-workloads --wait=true
```

The pod may run on the ordinary or CVM pool depending on scheduler scoring and available resources. Its toleration makes both user pools eligible; it does not express a confidentiality requirement. Never use a toleration by itself to assert that a sensitive workload runs on confidential hardware.

</details>

## 6. Inject a placement failure and recover it

**Task:** remove only the confidential deployment's permission to tolerate the CVM taint while preserving its CVM selector. Diagnose the stalled rollout from scheduler events, then restore the intended configuration. The ordinary workload must remain healthy throughout.

<details>
<summary>Solution</summary>

Patch the confidential deployment with an empty toleration list:

```powershell
kubectl patch deployment confidential-api -n mixed-workloads --type merge `
  -p '{"spec":{"template":{"spec":{"tolerations":[]}}}}'
Start-Sleep -Seconds 15
kubectl get pods -n mixed-workloads -o wide
kubectl get events -n mixed-workloads --sort-by=.lastTimestamp | Select-Object -Last 30
kubectl rollout status deployment/confidential-api -n mixed-workloads --timeout=30s
```

The rollout status should time out. The new pod requires `agentpool=cvm` but does not tolerate `workload-tier=confidential:NoSchedule`, so no eligible node exists. An old replica may remain Running because the Deployment rolling-update strategy preserves availability; do not mistake that for a successful rollout.

Confirm the ordinary deployment is unaffected:

```powershell
kubectl rollout status deployment/ordinary-api -n mixed-workloads --timeout=60s
kubectl get pods -n mixed-workloads -l app=ordinary-api -o wide
```

Restore the toleration:

```powershell
kubectl patch deployment confidential-api -n mixed-workloads --type merge `
  -p '{"spec":{"template":{"spec":{"tolerations":[{"key":"workload-tier","operator":"Equal","value":"confidential","effect":"NoSchedule"}]}}}}'
kubectl rollout status deployment/confidential-api -n mixed-workloads --timeout=300s
kubectl get pods -n mixed-workloads -o wide
```

The repaired confidential pod must run on the CVM pool, and ordinary pods must still run on the ordinary pool.

</details>

## 7. Stop paying for idle confidential capacity

**Task:** remove the confidential workload, verify that ordinary workloads remain healthy, and reduce the confidential pool to zero nodes. Then recreate the confidential workload and demonstrate a controlled scale-from-zero path. Record scale-up time and explain the availability tradeoff.

Use either the cluster autoscaler or an explicit scale command. Do not leave a sensitive deployment silently Pending without an alert or capacity process.

<details>
<summary>Solution</summary>

Delete only the confidential deployment. Disable autoscaling before an explicit manual scale operation, then scale the pool to zero:

```powershell
kubectl delete deployment confidential-api -n mixed-workloads --wait=true
kubectl rollout status deployment/ordinary-api -n mixed-workloads --timeout=60s

az aks nodepool update --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --disable-cluster-autoscaler
az aks nodepool scale --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --node-count 0
az aks nodepool show -g $ResourceGroup --cluster-name $ClusterName -n cvm `
  --query '{count:count,min:minCount,max:maxCount,autoscaling:enableAutoScaling}' -o table
kubectl get nodes -L agentpool,workload-tier
kubectl get pods -n mixed-workloads -o wide
```

Expected: the CVM pool count reaches zero and both ordinary replicas remain Running on the ordinary pool. Manually scaling a pool while its autoscaler is enabled creates competing owners, so the lab disables autoscaling for the explicit transition.

Re-enable autoscaling with zero as the minimum, reapply the confidential deployment from the original manifest, and observe scale from zero:

```powershell
az aks nodepool update --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --enable-cluster-autoscaler --min-count 0 --max-count 2
$Started = Get-Date
kubectl apply -f $MixedManifest
kubectl get pods -n mixed-workloads -w
```

In another terminal, observe autoscaler and pool state:

```powershell
kubectl get events -n mixed-workloads --sort-by=.lastTimestamp
az aks nodepool show -g $ResourceGroup --cluster-name $ClusterName -n cvm `
  --query '{count:count,min:minCount,max:maxCount,autoscaling:enableAutoScaling}' -o table
```

Stop the watch after the confidential pod is Running:

```powershell
$Elapsed = (Get-Date) - $Started
"Confidential scale-from-zero time: $($Elapsed.ToString())"
kubectl get pods -n mixed-workloads -o wide
```

If autoscaling does not begin within the approved observation window, inspect cluster-autoscaler status and scheduler events, then explicitly restore one node:

```powershell
az aks nodepool update --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --disable-cluster-autoscaler
az aks nodepool scale --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --node-count 1
kubectl rollout status deployment/confidential-api -n mixed-workloads --timeout=600s
```

Scale-to-zero saves idle CVM compute cost but adds cold-start time and depends on regional capacity. Workloads with strict availability or latency targets need warm capacity, multiple replicas, disruption controls, capacity reservations where appropriate, and a tested scaling policy. Autoscaling also does not decide whether a workload is confidential; selectors and policy still enforce that contract.

</details>

## 8. Add a policy and operating model

**Task:** define how a platform team will prevent misclassified workloads from bypassing the intended placement. Cover namespace or workload classification, allowed selectors and tolerations, image policy, resource limits, admission enforcement, autoscaling ownership, monitoring, and break-glass behavior.

Do not claim that taints are a security boundary by themselves.

<details>
<summary>Solution</summary>

A production operating model can use these controls:

| Concern | Recommended control |
|---|---|
| Workload classification | A reviewed label or namespace contract owned by security/data governance, not a free-form application choice. |
| Required CVM placement | Admission policy requires the approved CVM pool selector or required node affinity for confidential classifications. |
| Prevent ordinary usage | CVM pool uses `NoSchedule`; admission policy denies the CVM toleration and selector to unapproved workloads. Ordinary workloads require the ordinary pool or explicitly exclude CVM nodes. |
| Prevent bypass | Restrict who can create pods, edit controllers, mutate node labels/taints, or use `nodeName`. Audit privileged controllers and scheduler bypasses. |
| Supply chain | Approved immutable images, signature/provenance verification, vulnerability policy, least-privilege service accounts, and workload identity. |
| Runtime | Pod Security Standards, seccomp, capabilities, read-only filesystem where possible, network policy, egress control, and secret handling. |
| Capacity and cost | Platform-owned min/max values, alerts for idle CVM nodes and unschedulable confidential pods, measured cold-start SLO, and quota/capacity planning. |
| Evidence | Azure node-pool SKU and CVM image, pod-to-node mapping, policy decisions, attestation where required, and continuous configuration drift monitoring. |
| Break glass | Time-bound, approved, audited changes with automatic expiry and a tested return to enforced placement. |

Taints and selectors guide the scheduler. Users who can mutate workloads, nodes, admission configuration, or bind `nodeName` may bypass that intent. Enforce the model with Kubernetes/Azure authorization, admission policy, restricted node administration, and audit evidence.

For stronger label isolation, use an organization-prefixed key under the `node-restriction.kubernetes.io` namespace where appropriate and validate how AKS manages that label. The built-in `agentpool` label is convenient for the lab, while production policy should avoid trusting arbitrary mutable labels without an ownership model.

</details>

## Exit evidence

Retain:

- selected AKS version, region, ordinary and CVM SKU availability, quota, and restrictions;
- CVM node-pool `vmSize`, CVM node image, taint, label, and autoscaler configuration;
- pod-to-node evidence for ordinary and confidential deployments;
- the toleration-only observation;
- scheduler events from the missing-toleration failure and successful recovery;
- zero-node confidential-pool evidence, scale-from-zero duration, and ordinary workload continuity;
- the trust-boundary decision and proposed admission/operating model.

The lab is incomplete if a confidential pod merely “happened” to land on a CVM, if ordinary workloads can spill into the CVM pool, or if the evidence relies only on a custom node label.

## Customer conversation

<details>
<summary>Model answer: Is a toleration enough to mark a workload confidential?</summary>

No. A toleration permits a pod onto a tainted node but does not require that node. Use a required selector or node affinity to attract the workload, a taint to repel unapproved workloads, and admission policy plus RBAC to enforce who can request that placement.

</details>

<details>
<summary>Model answer: Are pods isolated from each other by the CVM hardware boundary?</summary>

The CVM protects the worker VM from the hypervisor and host management plane. Pods on that worker still share the node trust boundary and kernel in the ordinary container model. Use Kubernetes workload isolation controls and evaluate confidential containers separately if a per-pod utility-VM and attestation boundary is required.

</details>

<details>
<summary>Model answer: Should the confidential pool always scale to zero?</summary>

Only when cold-start delay, regional capacity risk, and the workload's SLO allow it. Zero idle nodes reduce cost but provide no warm confidential capacity. Critical services may need a nonzero minimum, multiple nodes and zones, disruption budgets, and tested failover.

</details>

<details>
<summary>Model answer: Does running on a CVM make the application compliant?</summary>

No. It is one technical control for data in use. Compliance depends on the complete system and evidence: identity, authorization, data handling, encryption, keys, software supply chain, network, storage, logging, operations, incident response, and the applicable governance interpretation.

</details>

<details>
<summary>Model answer: Can Intel TDX nodes be used instead?</summary>

Azure offers multiple confidential-computing technologies, but Intel TDX confidential VMs are not currently supported as AKS CVM node pools according to the reviewed AKS guidance. Discover current support before design; do not infer AKS support from general VM availability.

</details>

## Cleanup

Delete workloads first, scale down the confidential pool, inspect the dedicated resource group, and then remove it. Do not leave a CVM node running after the exercise.

<details>
<summary>Solution: complete teardown</summary>

```powershell
kubectl delete namespace mixed-workloads --ignore-not-found --wait=true
az aks nodepool update --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --disable-cluster-autoscaler
az aks nodepool scale --resource-group $ResourceGroup --cluster-name $ClusterName `
  --name cvm --node-count 0
az resource list --resource-group $ResourceGroup -o table
```

Only after confirming the resource group is the dedicated disposable lab boundary:

```powershell
az group delete --name $ResourceGroup
Remove-Item -Path $Work -Recurse -Force
```

The Azure command prompts for confirmation. After deletion completes, check Cost Management and confirm that no role assignments, monitoring resources, disks, snapshots, public IPs, or other artifacts created outside the resource group remain.

</details>

## References and support

Sources reviewed 2026-09-23:

- [Use Confidential Virtual Machines in AKS](https://learn.microsoft.com/azure/aks/use-cvm)
- [Azure confidential VM overview](https://learn.microsoft.com/azure/confidential-computing/confidential-vm-overview)
- [Confidential VM size options](https://learn.microsoft.com/azure/confidential-computing/virtual-machine-options)
- [AKS confidential containers overview](https://learn.microsoft.com/azure/aks/confidential-containers-overview)
- [AKS multiple node pools](https://learn.microsoft.com/azure/aks/create-node-pools)
- [AKS system node pools](https://learn.microsoft.com/azure/aks/use-system-pools)
- [AKS cluster autoscaler](https://learn.microsoft.com/azure/aks/cluster-autoscaler)
- [Kubernetes taints and tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/)
- [Kubernetes node selectors and affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)
- [Kubernetes NodeRestriction admission](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/#noderestriction)
