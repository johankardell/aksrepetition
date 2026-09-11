# Lab 6 — Scale APIs and event-driven workers within a cost envelope

**Scenario.** Interactive traffic needs ready API capacity; queued work can wait briefly and scale to zero. Demonstrate the different control loops, enforce bounds, and expose the boundary between retryable delivery and durable idempotency.

**Prerequisites/re-entry.** Complete labs 1–5, with healthy GitOps and observable API/worker traffic. Use PowerShell 7 on the private management host. Human platform access must permit add-on/pool configuration, managed-identity federation and queue-scoped role assignment. Never give these rights to the image publisher. The GA AKS Kubernetes version determines the supported managed KEDA version; do not install another KEDA Helm release.

**Architecture/ownership.** HPA controls `order-api` replicas from Metrics Server CPU; KEDA creates/owns the worker's HPA from queue length and activates it from zero. Cluster autoscaler (CA) adds/removes nodes for **unschedulable requested capacity**, not utilization alone. Flux owns autoscaler specifications and pod templates but **not Deployment replicas** after directive 3. KEDA polls Service Bus using its own operator service account's federation; the workload's federation alone is insufficient.

**Envelope and costs.** API 2–6 replicas, worker 0–5, apps pool min 2/max 4 nodes, unchanged three-node system pool, load ≤2,000 requests/≤20 concurrent with a ≤600-second request-start window, optional Spot pool 0–1 node, and one dedicated scaler managed identity. In-flight requests can finish after the load deadline (up to their 30-second timeout plus configured inter-request delay); it is not a process-kill timer. The job deadline bounds synthetic scheduling pressure. Verify regional D-family vCPU/Spot quota and subnet/OS-disk capacity before changing pools. The normal case is up to seven regular nodes (28 requested VM vCPUs if every node is D4) plus one optional Spot D4. Grafana, Firewall, Service Bus Premium, disks and private endpoints continue billing independently. Budget alerts are **not** spending caps, and quota is only one technical backstop.

**Important data limitation.** Before Lab 8, worker deduplication is **in-memory, per process**. It cannot guarantee duplicate-free effects across replicas, restarts or Spot eviction. This lab proves its boundary rather than claiming exactly-once delivery. The business-effect acceptance check is completed by re-running the duplicate/restart test after Lab 8's durable PostgreSQL unique-key writes. Only the lab's synthetic, disposable processing is interruption-tolerant today.

## Numbered directives

### 1. Establish bounds and capture the baseline

```powershell
. .\scripts\Use-Lab.ps1
flux get kustomizations
kubectl -n orders get deployments,hpa
kubectl -n orders top pods --containers
az aks nodepool list -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName `
  --query '[].{name:name,mode:mode,size:vmSize,count:count,min:minCount,max:maxCount,autoscaler:enableAutoScaling}' -o table
az vm list-usage --location $Lab.Location -o table
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName `
  --query '{version:kubernetesVersion,keda:workloadAutoScalerProfile.keda,oidc:oidcIssuerProfile.issuerUrl,workloadIdentity:securityProfile.workloadIdentity}' -o json
```

**Evidence:** no existing worker/API competing autoscalers and CPU metrics available. If `kubectl top` fails, fix Metrics Server first; managed Prometheus is not HPA's resource-metrics source. Existing `apps` nodes are regular Linux VMSS nodes on the Lab 3 UDR network. Save baseline pool counts and bounds in the execution record.

### 2. Enable supported managed KEDA and its operator federation

```powershell
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --enable-keda
az deployment group create -g $Lab.ResourceGroup -n scaling-identity `
  --template-file .\ops\scaling-identity.bicep `
  --parameters scalerIdentityName="$($Lab.Prefix)-scaler" serviceBusName=$Lab.ServiceBusName `
    location=$Lab.Location oidcIssuer=$Outputs.oidcIssuer.value
$Scaling = az deployment group show -g $Lab.ResourceGroup -n scaling-identity --query properties.outputs -o json | ConvertFrom-Json
$ScalerClientId = $Scaling.scalerClientId.value
kubectl -n kube-system annotate serviceaccount keda-operator `
  "azure.workload.identity/client-id=$ScalerClientId" --overwrite
kubectl -n kube-system rollout restart deployment/keda-operator
kubectl -n kube-system rollout status deployment/keda-operator --timeout=300s
kubectl -n kube-system get deployment keda-operator -o 'jsonpath={.spec.template.spec.containers[*].image}'
kubectl -n kube-system get pods -l app.kubernetes.io/name=keda-operator
```

Inspect the operator pod, not its Secret values:

```powershell
$KedaPod = kubectl -n kube-system get pods -l app.kubernetes.io/name=keda-operator -o 'jsonpath={.items[0].metadata.name}'
kubectl -n kube-system describe pod $KedaPod
```

**Evidence:** supported managed KEDA image, `azure.workload.identity/use` pod label, projected token volume and `AZURE_FEDERATED_TOKEN_FILE` environment variable. Do not print token contents. If missing, verify Workload ID was enabled **before** the add-on and restart the operator after annotating it. Confirm subject `system:serviceaccount:kube-system:keda-operator`, issuer and audience. Operator administration belongs to the platform, not application Flux.

The published Microsoft KEDA example uses **Azure Service Bus Data Owner** for runtime-property reads. This lab narrows that role to the **orders queue** and assigns it only to a **dedicated scaler identity**, federated to the KEDA operator. The application worker keeps its receiver-only permission and cannot exchange its service-account token for the scaler identity. Evaluate a narrower verified runtime-property role where supported; never elevate unrelated workloads to solve a scaler 403.

### 3. Transfer replica ownership and install separate autoscalers in Git

```powershell
$OrdersPath = '.\gitops\clusters\primary\apps\orders'
foreach ($file in 'api.yaml','worker.yaml') {
    $path = Join-Path $OrdersPath $file
    $text = Get-Content $path -Raw
    $text = $text -replace '(?m)^  replicas: \d+\s*\r?$', ''
    Set-Content $path $text -Encoding utf8
}
New-Item .artifacts\scaling -ItemType Directory -Force | Out-Null
$scaled = (Get-Content .\ops\scaling\worker-keda.yaml -Raw).
  Replace('__SCALER_CLIENT_ID__',$ScalerClientId).
  Replace('__SERVICEBUS_NAME__',$Lab.ServiceBusName)
Set-Content .artifacts\scaling\worker-keda.yaml $scaled -Encoding utf8
.\ops\Add-GitOpsFile.ps1 -Source .\ops\scaling\api-hpa.yaml
.\ops\Add-GitOpsFile.ps1 -Source .artifacts\scaling\worker-keda.yaml
.\ops\Add-GitOpsFile.ps1 -Source .\ops\scaling\worker-delay-patch.yaml -Kind Patch
kubectl kustomize $OrdersPath
git switch -c enable-bounded-scaling
git add gitops
git commit -m "Transfer replicas to HPA and KEDA with explicit limits"
git push -u origin HEAD
gh pr create --base main --fill
```

Review/merge, then:

```powershell
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
kubectl -n orders get hpa,scaledobject,triggerauthentication
kubectl -n orders describe scaledobject order-worker
kubectl -n orders get hpa -o 'jsonpath={range .items[*]}{.metadata.name}{" targets "}{.spec.scaleTargetRef.name}{"\n"}{end}'
```

**Evidence:** API HPA targets only `order-api`; KEDA-created HPA targets only `order-worker`. `ScaledObject Ready=True`; `Active=False` is normal for an empty queue. KEDA polls every 15 seconds, targets 10 active messages per worker and caps at five. Replica activation and HPA evaluation/cooldown are different delays. The two-second worker delay is synthetic and below the queue's one-minute lock; larger batches/delays require lock renewal and throughput analysis.

Flux must no longer reconcile `spec.replicas`. Watch after several reconciliation intervals; if scale repeatedly returns to a fixed count, find an old manifest, Helm value or Kustomize `replicas` transformer still setting it. Do not solve ownership conflict by leaving Flux disabled.

### 4. Demonstrate API HPA with bounded CPU demand

Use the real HTTPS application URL from Lab 3:

```powershell
$UserUri = (Read-Host 'Trusted HTTPS application URL').TrimEnd('/')
$BeforeApi = kubectl -n orders get deployment order-api -o 'jsonpath={.spec.replicas}'
$BeforeApi
```

In a second private-management terminal, create 180 seconds of CPU work inside **one** API container:

```powershell
$pod = kubectl -n orders get pods -l app=order-api -o 'jsonpath={.items[0].metadata.name}'
kubectl -n orders exec $pod -c api -- python -c `
  "import time; end=time.monotonic()+180; exec('while time.monotonic()<end:\n sum(range(10000))')"
```

In the first:

```powershell
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Operation Browse -Count 800 `
  -Concurrency 4 -DurationSeconds 180 -DelayMilliseconds 500 -OutputPath .artifacts\hpa-impact.json
kubectl -n orders get hpa order-api
kubectl -n orders describe hpa order-api
kubectl -n orders top pods --containers
kubectl -n orders get deployment order-api
```

**Expected:** utilization exceeds the 60% target relative to each pod's 250m CPU request, desired replicas rise within 2–6, and later decline only after the 180-second scale-down stabilization window. These are control-loop targets, not latency guarantees. Hold observations for a few more minutes; record current/desired replicas and actual external p95. If load is too light, don't increase limits arbitrarily—the explicit bounded CPU process is the deterministic demand source. One busy pod can skew average utilization; homogeneous requests and appropriate metrics matter.

### 5. Build and drain a finite queue backlog

```powershell
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Count 150 -Concurrency 6 `
  -DurationSeconds 60 -DelayMilliseconds 20 -OutputPath .artifacts\keda-load.json
for ($i=0; $i -lt 20; $i++) {
    kubectl -n orders get scaledobject order-worker
    kubectl -n orders get deployment order-worker
    az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName `
      --name orders --query '{active:countDetails.activeMessageCount,deadletter:countDetails.deadLetterMessageCount}' -o json
    Start-Sleep -Seconds 15
}
kubectl -n orders get hpa
```

**Evidence:** nonzero queue backlog activates KEDA, workers rise (never above five), queue depth drains and workers return to zero after cooldown/HPA stabilization. Portal/ARM queue counts are delayed; use multiple samples, KEDA external metrics, processing logs and timestamps, not a single count. Service Bus Data Owner is not a connection string. Scaling continues with local/SAS authentication disabled.

Do not interpret empty queue as durable order fulfillment before Lab 8. Rejected/dead-lettered messages can empty it too. Compare accepted count, processed synthetic IDs and dead-letter delta. If workload throughput is already high enough that backlog barely grows, the configured two-second delay should make the 150-message exercise observable; verify that patch is active before generating more messages.

### 6. Exercise a bounded node-scaling event

The foundation starts with three apps nodes; this exercise allows two to four. Changing minimum affects fault-domain capacity—two arbitrary nodes are not a guarantee of one node per zone. Retain three or more per the production availability design.

```powershell
$pool = az aks nodepool show -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps -o json | ConvertFrom-Json
if ($pool.enableAutoScaling) {
    az aks nodepool update -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps `
      --update-cluster-autoscaler --min-count 2 --max-count 4
} else {
    az aks nodepool update -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps `
      --enable-cluster-autoscaler --min-count 2 --max-count 4
}
$ImageReference = kubectl -n orders get deployment order-api -o 'jsonpath={.spec.template.spec.containers[0].image}'
$job = (Get-Content .\ops\scaling\capacity-job.yaml -Raw).Replace('__IMAGE_REFERENCE__',$ImageReference)
$job | kubectl apply -f -
for ($i=0; $i -lt 20; $i++) {
    kubectl get nodes -l agentpool=apps
    kubectl -n orders get pods -l job-name=bounded-capacity
    Start-Sleep -Seconds 20
}
kubectl -n orders get events --sort-by=.lastTimestamp | Select-String 'TriggeredScaleUp|FailedScheduling|NotTriggerScaleUp'
kubectl -n orders delete job bounded-capacity --ignore-not-found
```

**Expected:** twelve one-CPU reservation pods cause Pending pods when current allocatable apps CPU is insufficient; CA adds capacity but cannot exceed four apps nodes. Pods sleep rather than burn CPU—the point is **requests-based scheduling**. The job has a ten-minute deadline, five-minute task duration and five-minute TTL. Some pods can remain Pending at the cap; that is the guardrail working, not proof that max should be raised. If you already have enough larger nodes, inspect allocatable resources and state why no scale-up was needed rather than inventing one.

Observe contraction over the CA scale-down window after deleting the job:

```powershell
kubectl -n kube-system get configmap cluster-autoscaler-status -o yaml
az aks nodepool show -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps `
  --query '{count:count,min:minCount,max:maxCount}' -o json
kubectl get nodes
```

**Solution for a blocked scale:** `Insufficient cpu` with max reached is capacity policy; taints/selectors/zone constraints can be impossible to satisfy with this pool; quota or allocation failure is Azure capacity. PDBs, local data and non-evictable pods can delay scale-down. Fix the actual reason. CA is not a repair for an overloaded database or a queue service throughput limit.

### 7. Break KEDA authentication in a reversible way

Stop load and let the queue drain first. Suspend the application child only; the root platform Kustomization continues to run:

```powershell
flux suspend kustomization orders
try {
    kubectl -n orders patch triggerauthentication servicebus-workload --type=merge `
      -p '{"spec":{"podIdentity":{"identityId":"00000000-0000-0000-0000-000000000000"}}}'
    Start-Sleep -Seconds 45
    kubectl -n orders describe scaledobject order-worker
    kubectl -n kube-system logs deployment/keda-operator --since=3m --tail=100
    kubectl -n orders get hpa
} finally {
    flux resume kustomization orders
    flux reconcile kustomization orders --with-source
}
kubectl -n orders describe scaledobject order-worker
```

**Expected:** scaler authentication error/Ready condition changes; no successful automatic activation should be assumed while it is unhealthy. Existing workers may continue because their **own** Workload ID client ID is unchanged. Logs distinguish a missing/incorrect client or federation (`AADSTS…`) from a valid token with Service Bus 403, and both from DNS/TCP timeout.

**Solution:** restoring the Git identityId fixes only this injection. Real failures also require matching issuer/subject/audience and Entra propagation. Do not delete the working application federation or add static secrets. Keep the KEDA operator healthy during AKS maintenance and recheck its annotation/projection after add-on updates.

### 8. Prove duplicate handling, restart limitations, retry and dead-letter paths

Use a single paused worker to make the in-memory boundary deterministic. KEDA's pause annotation, not a second hand-written HPA, sets one replica:

```powershell
flux suspend kustomization orders
kubectl -n orders annotate scaledobject order-worker 'autoscaling.keda.sh/paused-replicas=1' --overwrite
Start-Sleep -Seconds 30
kubectl -n orders rollout status deployment/order-worker --timeout=300s
$DuplicateId = "duplicate-$([guid]::NewGuid().ToString('N'))"
$body = @{id=$DuplicateId;item='synthetic-repeat'} | ConvertTo-Json -Compress
Invoke-RestMethod "$UserUri/orders" -Method Post -ContentType application/json -Body $body
Start-Sleep -Seconds 10
Invoke-RestMethod "$UserUri/orders" -Method Post -ContentType application/json -Body $body
Start-Sleep -Seconds 10
kubectl -n orders logs deployment/order-worker --since=5m | Select-String $DuplicateId
```

**Expected before Lab 8:** same process logs first `duplicate:false`, then `duplicate:true`, both `durable:false`. Now restart and resend:

```powershell
kubectl -n orders rollout restart deployment/order-worker
kubectl -n orders rollout status deployment/order-worker --timeout=300s
Invoke-RestMethod "$UserUri/orders" -Method Post -ContentType application/json -Body $body
Start-Sleep -Seconds 10
kubectl -n orders logs deployment/order-worker --since=3m | Select-String $DuplicateId
```

**Expected before Lab 8:** `duplicate:false` again—the state was lost. This is the explicit negative check, **not a successful exactly-once demonstration**. After Lab 8, repeat unchanged: the same ID must log duplicate true after restart, `durable:true`, and GET `/orders/{id}` must show one persisted business row. A PK alone cannot atomically cover unrelated side effects such as charging a card; use an outbox/inbox or downstream idempotency key where required.

Send a poison message directly with the already-authorized API identity, bypassing the API's validation only for this controlled test:

```powershell
$Poison = @'
from app import bus_client
from azure.servicebus import ServiceBusMessage
with bus_client() as client, client.get_queue_sender(queue_name="orders") as sender:
    sender.send_messages(ServiceBusMessage('{"id":"","item":"synthetic-invalid"}'))
'@
kubectl -n orders exec deployment/order-api -c api -- python -c $Poison
Start-Sleep -Seconds 10
$Peek = @'
from app import bus_client
from azure.servicebus import ServiceBusSubQueue
with bus_client() as client, client.get_queue_receiver(queue_name="orders", sub_queue=ServiceBusSubQueue.DEAD_LETTER) as receiver:
    for message in receiver.peek_messages(max_message_count=10):
        print(message.dead_letter_reason, str(message))
'@
kubectl -n orders exec deployment/order-worker -c worker -- python -c $Peek
az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName `
  --name orders --query '{maxDelivery:maxDeliveryCount,lock:lockDuration,deadletter:countDetails.deadLetterMessageCount}' -o json
```

**Evidence:** invalid payload is explicitly dead-lettered with `InvalidOrder`; this is **not** evidence of max-delivery retry exhaustion. Uncompleted messages after a crash are redelivered when the lock expires and can eventually reach max delivery count. To observe an interrupted receive, enqueue a fresh synthetic order with the two-second processing delay active and delete only its worker pod during processing; after replacement/lock expiry, correlate its processing log. The exact kill timing is nondeterministic—don't claim a duplicate when the pod completed before deletion.

Always finish by removing the temporary pause explicitly (Flux does not necessarily own arbitrary live annotations):

```powershell
kubectl -n orders annotate scaledobject order-worker autoscaling.keda.sh/paused-replicas-
flux resume kustomization orders
flux reconcile kustomization orders --with-source
```

If interrupted during this directive, these are the **first re-entry cleanup commands**. Leave dead-letter evidence available for Lab 8/incident debrief; any replay or purge must target only the test messages with deliberate receive/complete logic, not delete/recreate the queue.

### 9. Place only the synthetic worker on bounded Spot capacity and test interruption

This branch creates at most **one** additional billable node and requires available regional Spot quota/capacity. Run it only within the approved footprint; if unavailable, retain regular workers and record Spot as **not executed**, not “passed.” The API and system components remain on regular pools.

```powershell
az aks nodepool add -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n workerspot `
  --mode User --priority Spot --eviction-policy Delete --spot-max-price -1 `
  --node-vm-size Standard_D4ds_v5 --os-sku Ubuntu --node-count 1 `
  --enable-cluster-autoscaler --min-count 0 --max-count 1 `
  --vnet-subnet-id $Outputs.nodesSubnetId.value `
  --node-taints kubernetes.azure.com/scalesetpriority=spot:NoSchedule
.\ops\Add-GitOpsFile.ps1 -Source .\ops\scaling\spot-worker-patch.yaml -Kind Patch
git switch -c exercise-spot-worker
git add gitops
git commit -m "Place interruption-tolerant synthetic workers on bounded Spot"
git push -u origin HEAD
gh pr create --base main --fill
```

`--spot-max-price -1` means price up to the on-demand price without price-based eviction; it does **not** guarantee allocation or protect against capacity eviction. Node count is bounded but disk/log/network charges are additional. Use an explicit numeric maximum only after checking current regional pricing and accepting higher eviction probability.

After merge:

```powershell
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Count 100 -Concurrency 4 `
  -DurationSeconds 60 -OutputPath .artifacts\spot-load.json
kubectl -n orders get pods -l app=order-worker -o wide
kubectl get nodes -l agentpool=workerspot -o wide
```

Wait until a Spot worker is Running, then simulate **one Spot VM eviction**, deriving the exact backing VM rather than editing arbitrary AKS VMSS settings:

```powershell
$SpotNode = kubectl get nodes -l agentpool=workerspot -o 'jsonpath={.items[0].metadata.name}'
if (-not $SpotNode) { throw 'No Spot node; do not simulate against a regular pool.' }
$ProviderId = kubectl get node $SpotNode -o 'jsonpath={.spec.providerID}'
$VmResourceId = $ProviderId -replace '^azure://', ''
if ($VmResourceId -notmatch '/virtualMachineScaleSets/[^/]+/virtualMachines/\d+$') { throw 'Unexpected provider ID' }
az vmss simulate-eviction --ids $VmResourceId
Start-Sleep -Seconds 45
kubectl get nodes -l agentpool=workerspot
kubectl -n orders get pods -l app=order-worker -o wide
kubectl -n orders get events --sort-by=.lastTimestamp
```

**Evidence:** scheduled eviction affects only the Spot worker node; pending/replacement workers and queue backlog reflect interruption. Spot replacement is **not guaranteed**, regardless of autoscaler demand. Kubernetes graceful handling and the worker's 45-second termination grace are best effort; sudden termination may provide less. At-least-once delivery plus process-local dedup is not safe for irreversible production effects. Preserve API SLI evidence while workers recover or remain Pending.

**Solution/regular fallback:** remove `spot-worker-patch.yaml` and its `patches` entry from the Git-owned orders directory via a reviewed PR, merge and reconcile. This restores the original `agentpool: apps` selector; no automatic regular-capacity fallback was configured. Verify worker placement/backlog draining, then delete only the optional Spot pool:

```powershell
git switch -c return-workers-to-regular
.\ops\Remove-GitOpsFile.ps1 -Name spot-worker-patch.yaml
git add gitops
git commit -m "Return workers to regular capacity after Spot exercise"
git push -u origin HEAD
gh pr create --base main --fill
# After review/merge:
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
kubectl -n orders get pods -l app=order-worker -o wide
az aks nodepool delete -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n workerspot
```

Do not delete Spot capacity before returning workers to regular pools, or silently add the Spot toleration to all application pods.

### 10. Compare NAP/Automatic and reset without losing the scaling design

```powershell
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName `
  --query '{provisioning:nodeProvisioningProfile,outbound:networkProfile.outboundType,network:networkProfile.networkPlugin,mode:networkProfile.networkPluginMode,dataplane:networkProfile.networkDataplane}' -o json
kubectl -n orders get hpa,scaledobject,deployments
kubectl -n orders delete job bounded-capacity --ignore-not-found
flux get kustomizations
```

**Required comparison artifact:** record observed trigger, delay, maximum capacity, post-load contraction, cost drivers and failure domain for each actual loop. Use this model:

| Choice | Scheduling/cost behavior | Operational responsibility and constraints |
|---|---|---|
| HPA / KEDA | Changes pods within explicit bounds; KEDA scales an idle worker to zero | You choose requests, metrics, lag objective, concurrency and backend headroom. A zero-pod worker still leaves node/platform cost. |
| CA on fixed pools (executed) | Adds instances of pool VM shapes for Pending pods; consolidates only within those shapes | You own pool SKUs/min/max, regular/Spot placement, disruption budgets and upgrade settings. |
| NAP on Standard (comparison) | Karpenter-based NodePools/AKSNodeClasses choose VM shapes for Pending requests, with provisioning and disruption policies | Not “HPA for nodes.” It changes node lifecycle/VM selection, must be explicitly constrained, and cannot be combined with CA managing the same cluster's workload capacity. |
| AKS Automatic (comparison) | NAP preconfigured; a managed baseline reduces pool tuning | Evaluate its supported customization and current managed-system-node behavior. Customer code, data integrity, SLOs, scaling intent and spend still belong to you. |

NAP is a current service capability, not categorically “still preview.” However, on the authoring CLI 2.90, `az aks ... --node-provisioning-mode` and `--node-provisioning-default-pools` help are still marked **Preview**. This required path therefore uses GA CA commands and does not demand preview registration/extension installation. Check the live service/CLI matrix before a separate NAP implementation. The official service restrictions reviewed on 2026-09-10 include **no Windows pools, no IPv6, managed identity required, no stop/start, no change of outbound type after enabling, Standard Load Balancer required for a custom VNet**. Do not enable NAP on the cumulative cluster: Lab 3 deliberately changed outbound type, and later labs expect regular pool operations.

If conducting the **optional separate comparison**, obtain a separate resource group and approved budget; create a private, Linux, Cilium Overlay, managed-identity cluster with its **final UDR network selected at creation**, no CA, and bounded NodePool aggregate CPU/memory and allowed VM SKUs. Use current supported NAP provisioning documentation, not a copied preview CLI command. Start with no Spot, constrain to a small D-family set and a maximum equivalent to two D4 workload nodes, reproduce this job's resource pressure at that smaller bound, inspect `NodeClaim`/`NodePool` decisions and consolidation, then **delete** the whole comparison footprint that day. NAP NodePool limits can be exceeded briefly during parallel provisioning, so combine quota, allowed SKUs and cost monitoring. This comparison is not credited as deployed unless its provisioning/teardown evidence exists; the capstone never depends on it.

For normal reset, remove the two-second `worker-delay-patch.yaml` and its `patches` entry from Git through review, merge, reconcile, then confirm empty active queue and healthy autoscalers. Retain HPA/KEDA and omitted Deployment replicas for later labs. If baseline resilience calls for three regular apps nodes, restore CA minimum three while retaining maximum four:

```powershell
git switch -c restore-normal-worker-throughput
.\ops\Remove-GitOpsFile.ps1 -Name worker-delay-patch.yaml
git add gitops
git commit -m "Remove synthetic processing delay"
git push -u origin HEAD
gh pr create --base main --fill
# After review/merge:
git switch main
git pull --ff-only
az aks nodepool update -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps `
  --update-cluster-autoscaler --min-count 3 --max-count 4
flux reconcile kustomization orders --with-source
```

For **final teardown only**, remove HPA/ScaledObject/TriggerAuthentication through Git before disabling KEDA; restore intentional fixed replicas if keeping the workloads. Remove the dedicated scaler identity's `keda-operator` federation and the `scaling-identity` deployment's recorded `scalerRoleId` role assignment, then delete that scaler identity (the worker and its receiver role belong to foundation). Then run `az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --disable-keda` and follow root cleanup. Deleting a Bicep deployment record does **not** delete its resources. Persistent nodes, disks, messages and monitoring charges outlive a stopped load generator.

## Customer discovery and model answers

| Question | Model answer |
|---|---|
| Why not CPU-scale both components? | APIs often need standing latency headroom; workers need to drain backlog within an objective. Queue length/age may represent worker demand better than CPU. |
| Who decides replica count? | One autoscaling owner per Deployment. Flux owns policy; HPA controls API replicas; KEDA controls worker activation and its generated HPA. |
| Why are pods Pending with low node CPU? | Scheduling uses requests, placement constraints and allocatable resources; actual utilization is a different measurement. |
| Does Spot plus retries guarantee correct orders? | No. At-least-once delivery requires durable idempotent business effects. Before Lab 8 this app's memory-only dedup fails after restart or across replicas. |
| Does a two-minute cooldown guarantee completion? | No. Queue arrival rate, processing rate, locks, max replicas, external quota and node allocation all matter. Measure oldest-message age and business completion. |
| Will NAP or Automatic always be cheaper? | Not necessarily. They alter selection/consolidation and operational work; workload shape, constraints, commitments, disruptions and continuously billable services determine total cost. |
| Does the worker need Data Owner for scaling? | No. The worker remains receiver-only; a separate operator-federated scaler identity holds the queue-scoped runtime-property role. Scaling must not unnecessarily broaden application privileges. |

## References and support

Source review: **2026-09-10**. HPA autoscaling/v2, cluster autoscaler, managed KEDA add-on/Workload ID and Spot node pools are the required supported features. `keda.sh/v1alpha1` is KEDA's published stable operational CRD API name; it does not mean this lab requires an AKS preview. Validate the KEDA minor mapped to the chosen GA Kubernetes version at execution. No capacity, eviction, scaling or Azure role changes were executed while authoring.

- [Managed KEDA with Workload ID](https://learn.microsoft.com/en-us/azure/aks/keda-workload-identity)
- [KEDA Service Bus scaler](https://keda.sh/docs/2.18/scalers/azure-service-bus/) — compare with the actual managed version
- [AKS cluster autoscaler](https://learn.microsoft.com/en-us/azure/aks/cluster-autoscaler)
- [Kubernetes HPA behavior](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [AKS Spot node pools](https://learn.microsoft.com/en-us/azure/aks/spot-node-pool)
- [Azure VMSS simulated eviction](https://learn.microsoft.com/en-us/cli/azure/vmss#az-vmss-simulate-eviction)
- [NAP prerequisites and limitations](https://learn.microsoft.com/en-us/azure/aks/node-auto-provisioning)
- [AKS Automatic](https://learn.microsoft.com/en-us/azure/aks/intro-aks-automatic)
