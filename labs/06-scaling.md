# Lab 6 — Scale APIs and event-driven workers within a cost envelope

**Scenario.** Interactive traffic needs ready API capacity; queued work can wait briefly and scale to zero. Demonstrate the different control loops, enforce bounds, and expose the boundary between retryable delivery and durable idempotency.

**Prerequisites/re-entry.** Complete labs 1–5, with healthy GitOps and observable API/worker traffic. Use PowerShell 7 on the private management host. Human platform access must permit add-on/pool configuration, managed-identity federation and queue-scoped role assignment. Never give these rights to the image publisher. The GA AKS Kubernetes version determines the supported managed KEDA version; do not install another KEDA Helm release.

**Architecture/ownership.** HPA controls `order-api` replicas from Metrics Server CPU; KEDA creates/owns the worker's HPA from queue length and activates it from zero. Cluster autoscaler (CA) adds/removes nodes for **unschedulable requested capacity**, not utilization alone. Flux owns autoscaler specifications and pod templates but **not Deployment replicas** after directive 3. KEDA polls Service Bus using its own operator service account's federation; the workload's federation alone is insufficient.

**Envelope and costs.** API 2–6 replicas, worker 0–5, apps pool min 2/max 4 nodes, unchanged three-node system pool, load ≤2,000 requests/≤20 concurrent with a ≤600-second request-start window, optional Spot pool 0–1 node, and one dedicated scaler managed identity. In-flight requests can finish after the load deadline (up to their 30-second timeout plus configured inter-request delay); it is not a process-kill timer. The job deadline bounds synthetic scheduling pressure. Verify regional D-family vCPU/Spot quota and subnet/OS-disk capacity before changing pools. The normal case is up to seven regular nodes (28 requested VM vCPUs if every node is D4) plus one optional Spot D4. Grafana, Firewall, Service Bus Premium, disks and private endpoints continue billing independently. Budget alerts are **not** spending caps, and quota is only one technical backstop.

**Important data limitation.** Before Lab 8, worker deduplication is **in-memory, per process**. It cannot guarantee duplicate-free effects across replicas, restarts or Spot eviction. This lab proves its boundary rather than claiming exactly-once delivery. The business-effect acceptance check is completed by re-running the duplicate/restart test after Lab 8's durable PostgreSQL unique-key writes. Only the lab's synthetic, disposable processing is interruption-tolerant today.

## Numbered directives

### 1. Establish bounds and capture the baseline

**Challenge:** capture the current workload, resource-metrics and pool baseline, verify quota/headroom, and confirm the cluster is ready for bounded HPA, KEDA and node-scaling experiments.

**Constraints:** use the approved envelope above and save baseline pool counts/bounds before changes. Stop if Metrics Server is unavailable or competing autoscalers already exist. The apps pool must remain regular Linux VMSS on the Lab 3 UDR network.

**Exit evidence:** retain current Deployments/autoscalers, container CPU metrics, pool sizes/min/max, regional quota and Kubernetes/KEDA/OIDC/Workload ID settings.

<details>
<summary>Solution</summary>

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

Compare quota usage plus the permitted extra node vCPUs with both total regional and applicable VM-family limits. Quota availability is necessary but does not guarantee Azure allocation or zone capacity. Record existing replica counts and Git revisions so later changes can be attributed to the correct control loop.

</details>

### 2. Enable supported managed KEDA and its operator federation

**Challenge:** enable the managed KEDA add-on and configure a dedicated queue-scoped scaler identity federated to the operator, then verify token projection without exposing credentials.

**Ownership/constraints:** the platform administers the operator, not application Flux. Use the AKS-supported managed KEDA version, never a competing Helm release. Do not print token contents. The documented runtime-property role is Service Bus Data Owner scoped only to the `orders` queue on the dedicated scaler identity; keep application workers receiver-only. Evaluate narrower verified roles where supported, never elevate unrelated workloads to fix a scaler 403.

**Exit evidence:** capture the supported operator image, service-account annotation, Workload ID label/projected-token metadata and matching issuer/subject/audience plus queue-scoped role assignment.

<details>
<summary>Solution</summary>

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

</details>

### 3. Transfer replica ownership and install separate autoscalers in Git

**Challenge:** remove Git ownership of Deployment replicas and install the API HPA, worker KEDA configuration and temporary processing delay through a reviewed Git change.

**Constraints:** use one autoscaling owner per Deployment. Flux retains autoscaler/template ownership but must not enforce replicas; never resolve ownership conflicts by leaving Flux disabled. The synthetic worker delay is two seconds, below the one-minute queue lock; larger batches/delays require lock-renewal and throughput analysis.

**Exit evidence:** retain the reviewed diff, rendered manifests, distinct HPA targets, healthy ScaledObject and observations across several Flux intervals proving replicas are no longer reset by Git.

<details>
<summary>Solution</summary>

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

</details>

### 4. Demonstrate API HPA with bounded CPU demand

**Challenge:** generate bounded demand on one API container, measure external HTTPS impact and observe API scale-up/down while explaining the relationship between CPU requests and HPA targets.

**Constraints:** keep API replicas within 2–6 and use the fixed 180-second CPU process, not arbitrary increases to limits/load. Use the real trusted Lab 3 HTTPS URL. Control-loop settings do not guarantee latency, and one hot pod can skew an average.

**Exit evidence:** record pre-load/current/desired replicas, CPU/request utilization, external p95/availability, scaling timestamps and post-load contraction across the stabilization window.

<details>
<summary>Solution</summary>

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

Observe `kubectl -n orders get hpa order-api` and container CPU from another terminal during the run as well as afterward. HPA uses the average CPU utilization relative to requests, not percentage of the node's CPU or the pod limit. As replicas are added, the same single hot pod contributes less to that average; reaching six replicas is not required for a successful bounded-scaling demonstration.

</details>

### 5. Build and drain a finite queue backlog

**Challenge:** build a finite synthetic backlog, observe worker activation and bounded scaling, then verify processing/drain and eventual return to zero.

**Constraints:** keep the 150-message bounded run and five-worker cap. Verify the temporary two-second delay before increasing load. Preserve disabled local/SAS authentication. Empty queue depth is not durable fulfillment; rejected or dead-lettered messages can empty it too.

**Exit evidence:** retain timestamped queue/external-metric samples, worker replicas and processing logs; compare accepted count, processed IDs and dead-letter delta through activation and cooldown.

<details>
<summary>Solution</summary>

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

Capture worker logs while replicas are active; logs from scaled-to-zero pods must be queried through the Lab 5 log workspace. Use `.artifacts\keda-load.json` request IDs to identify this run rather than treating all queue activity as yours. A short backlog can trigger only a small replica increase; activation, healthy scaling and eventual drain within the cap are the required observations, not necessarily all five workers.

</details>

### 6. Exercise a bounded node-scaling event

**Challenge:** configure the apps pool within its approved node bounds, generate finite requested-capacity pressure, and distinguish scheduling/capacity limits from utilization before observing contraction.

The foundation starts with three apps nodes; this exercise allows two to four. Changing minimum affects fault-domain capacity—two arbitrary nodes are not a guarantee of one node per zone. Retain three or more per the production availability design.

**Constraints:** keep max four apps nodes and the job's ten-minute deadline/five-minute TTL. Do not increase the cap to make all pods schedule. Delete the synthetic job after observation; report sufficient existing capacity honestly instead of inventing a scale-up.

**Exit evidence:** record Pending/scheduled pods and scheduler events, requested versus allocatable capacity, actual node counts and CA status before/during/after load. Explain any blocked scale-up or delayed scale-down.

<details>
<summary>Solution</summary>

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

For a blocked scale, `Insufficient cpu` with max reached is capacity policy; taints/selectors/zone constraints can be impossible to satisfy with this pool; quota or allocation failure is Azure capacity. PDBs, local data and non-evictable pods can delay scale-down. Fix the actual reason. CA is not a repair for an overloaded database or a queue service throughput limit.

</details>

### 7. Break KEDA authentication in a reversible way

**Challenge:** inject an invalid scaler identity, distinguish authentication, authorization and connectivity failures, and restore healthy activation without changing the worker's identity.

**Constraints:** stop load and drain the queue first. Suspend only the application child while the platform root continues running; always resume/reconcile afterward. Do not delete the working application federation or add static secrets. Recheck operator annotation/token projection after AKS add-on updates.

**Exit evidence:** retain the failed ScaledObject condition/operator logs, existing worker behavior and HPA status, followed by restored Git identity and healthy scaler conditions.

<details>
<summary>Solution</summary>

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

Restoring the Git identityId fixes only this injection. Real failures also require matching issuer/subject/audience and Entra propagation. Do not delete the working application federation or add static secrets. Keep the KEDA operator healthy during AKS maintenance and recheck its annotation/projection after add-on updates.

</details>

### 8. Prove duplicate handling, restart limitations, retry and dead-letter paths

**Challenge:** use a single temporarily paused worker to compare same-process and post-restart duplicate handling, inspect a controlled poison message and attempt an interrupted receive/retry. Repeat the business-effect acceptance check after Lab 8.

**Constraints:** use only synthetic disposable messages and the existing API/worker identities. Pause with KEDA's annotation, not another HPA. Never claim exactly-once behavior before durable storage; even a database PK cannot atomically cover unrelated external effects. Pod-interruption timing is nondeterministic, so report an inconclusive retry honestly.

**Exit evidence:** retain matching IDs and duplicate/durable flags before/after restart, dead-letter reason/count delta and an honestly classified retry attempt. After Lab 8 require a duplicate after restart and one persisted business row. Leave dead-letter evidence for Lab 8/debrief; replay/purge only identified test messages, never delete/recreate the queue.

**Mandatory recovery/re-entry:** remove the temporary KEDA pause and resume/reconcile `orders` before leaving this directive. If interrupted, use the cleanup at the end of the solution first; Flux may not own arbitrary live annotations.

<details>
<summary>Solution</summary>

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

Only when repeating after Lab 8, verify the durable lookup for that same ID:

```powershell
Invoke-RestMethod "$UserUri/orders/$DuplicateId"
```

The returned ID/item must match the repeated payload and the durable worker logs. Before Lab 8 this endpoint intentionally returns 503; that is not a failure of this lab's negative check.

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

With the active queue drained and the worker still paused at one replica, make one bounded interruption attempt:

```powershell
$WorkerPods = kubectl -n orders get pods -l app=order-worker -o json | ConvertFrom-Json
if (@($WorkerPods.items).Count -ne 1) { throw 'Wait for exactly one worker before the interruption test.' }
$WorkerPod = $WorkerPods.items[0].metadata.name
$RetryId = "retry-$([guid]::NewGuid().ToString('N'))"
$RetryBody = @{id=$RetryId;item='synthetic-interruption'} | ConvertTo-Json -Compress
Invoke-RestMethod "$UserUri/orders" -Method Post -ContentType application/json -Body $RetryBody
kubectl -n orders delete pod $WorkerPod --wait=false
Start-Sleep -Seconds 75
kubectl -n orders rollout status deployment/order-worker --timeout=300s
kubectl -n orders logs deployment/order-worker --since=5m | Select-String $RetryId
```

Normal pod deletion sends SIGTERM. This worker abandons its in-flight message if interrupted during the synthetic delay, allowing redelivery before lock expiry; a hard crash instead leaves an uncompleted lock. No retry is proven if the message was not received before deletion or completed first. A replacement's processed log proves eventual processing, not by itself a prior receive or max-delivery exhaustion. Correlate retained traces/logs from the old pod with the replacement and classify absent receive evidence as inconclusive. Do not repeatedly kill pods to force a claimed result.

Always finish by removing the temporary pause explicitly (Flux does not necessarily own arbitrary live annotations):

```powershell
kubectl -n orders annotate scaledobject order-worker autoscaling.keda.sh/paused-replicas-
flux resume kustomization orders
flux reconcile kustomization orders --with-source
```

If interrupted during this directive, these are the **first re-entry cleanup commands**. Leave dead-letter evidence available for Lab 8/incident debrief; any replay or purge must target only the test messages with deliberate receive/complete logic, not delete/recreate the queue.

</details>

### 9. Place only the synthetic worker on bounded Spot capacity and test interruption

**Challenge:** on the optional approved branch, place only synthetic workers on one bounded Spot node, simulate one verified Spot eviction, measure the effect, then return workers to regular capacity through Git.

This branch creates at most **one** additional billable node and requires available regional Spot quota/capacity. Run it only within the approved footprint; if unavailable, retain regular workers and record Spot as **not executed**, not “passed.” The API and system components remain on regular pools.

**Constraints:** `--spot-max-price -1` permits up to on-demand price without price-based eviction, not guaranteed allocation or protection from capacity eviction; disks/logs/network cost extra. An explicit numeric maximum requires current regional pricing and acceptance of higher eviction probability. Target only the verified Spot VM, never arbitrary AKS VMSS settings. Replacement/graceful termination are not guaranteed; process-local dedup is unsafe for irreversible production effects.

**Exit evidence:** record worker-only placement, the exact evicted Spot node/VM, API SLIs and backlog/replacement behavior. Restore the original regular-pool selector through review and verify worker placement/drain before deleting only the optional pool; never broaden Spot tolerations to all pods.

<details>
<summary>Solution</summary>

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

For regular fallback, remove `spot-worker-patch.yaml` and its `patches` entry from the Git-owned orders directory via a reviewed PR, merge and reconcile. This restores the original `agentpool: apps` selector; no automatic regular-capacity fallback was configured. Verify worker placement/backlog draining, then delete only the optional Spot pool:

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

</details>

### 10. Compare NAP/Automatic and reset without losing the scaling design

**Challenge:** compare pod scaling, fixed-pool CA, NAP on Standard and AKS Automatic using observed evidence where executed and clearly labeled design analysis otherwise. Reset synthetic changes without losing autoscaler ownership.

**Required comparison artifact:** record trigger, observed delay, maximum capacity, post-load contraction, cost drivers and failure domain for each actual loop. Contrast scheduling/VM selection and operational responsibility for NAP/Automatic; do not present a comparison as an executed deployment.

**Constraints:** do not enable NAP on this cumulative cluster or combine it with CA for the same cluster's workload capacity. The required path uses GA CA commands; on authoring CLI 2.90 the NAP flags `--node-provisioning-mode` and `--node-provisioning-default-pools` remain marked Preview even though NAP is a current service capability. Check the live matrix before a separate implementation.

The reviewed NAP restrictions include no Windows pools, no IPv6, managed identity required, no stop/start, no outbound-type change after enabling, and Standard Load Balancer for a custom VNet. Lab 3 changed outbound type and later labs expect regular pool operations. An optional comparison requires a **separate** resource group/budget, private Linux Cilium Overlay with final UDR networking at creation, no CA, bounded aggregate CPU/memory and allowed SKUs, initially no Spot and at most two D4-equivalent workload nodes. Use current supported provisioning documentation, not copied preview commands. Delete that footprint the same day; NodePool limits may briefly overshoot during parallel provisioning, so also constrain quota/SKUs and monitor cost. The capstone never depends on this optional branch.

**Exit evidence:** retain the comparison, cleanup PR and healthy autoscaler/reconciler state, with empty active queue and synthetic job/delay/Spot configuration removed. Keep HPA/KEDA and omitted Deployment replicas for later labs; restore minimum three regular apps nodes if required by baseline resilience, retaining max four.

Final teardown is only at course end: remove autoscaling resources through Git before disabling KEDA, restore fixed replicas if retaining workloads, and remove only the dedicated scaler federation/role/identity. Do not remove the foundation worker identity/receiver role. Deleting a Bicep deployment record does not delete resources; persistent nodes/disks/messages/monitoring continue billing after load stops.

<details>
<summary>Solution</summary>

```powershell
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName `
  --query '{provisioning:nodeProvisioningProfile,outbound:networkProfile.outboundType,network:networkProfile.networkPlugin,mode:networkProfile.networkPluginMode,dataplane:networkProfile.networkDataplane}' -o json
kubectl -n orders get hpa,scaledobject,deployments
kubectl -n orders delete job bounded-capacity --ignore-not-found
flux get kustomizations
```

Use the following model for the design comparison, then attach measured timestamps and counts from your own run:

| Choice | Scheduling/cost behavior | Operational responsibility and constraints |
|---|---|---|
| HPA / KEDA | Changes pods within explicit bounds; KEDA scales an idle worker to zero | You choose requests, metrics, lag objective, concurrency and backend headroom. A zero-pod worker still leaves node/platform cost. |
| CA on fixed pools (executed) | Adds instances of pool VM shapes for Pending pods; consolidates only within those shapes | You own pool SKUs/min/max, regular/Spot placement, disruption budgets and upgrade settings. |
| NAP on Standard (comparison) | Karpenter-based NodePools/AKSNodeClasses choose VM shapes for Pending requests, with provisioning and disruption policies | Not “HPA for nodes.” It changes node lifecycle/VM selection, must be explicitly constrained, and cannot be combined with CA managing the same cluster's workload capacity. |
| AKS Automatic (comparison) | NAP preconfigured; a managed baseline reduces pool tuning | Evaluate its supported customization and current managed-system-node behavior. Customer code, data integrity, SLOs, scaling intent and spend still belong to you. |

For the executed control loops, a complete model interpretation is:

- **API HPA:** CPU/request utilization triggers pod changes between two and six; record metric arrival and desired/actual replica timestamps, not an assumed response time. Scale-down uses a 180-second stabilization window. Failure domains include unavailable resource metrics and insufficient schedulable capacity; extra replicas consume capacity but do not automatically add nodes.
- **Worker KEDA:** active queue demand activates replicas from zero and its generated HPA scales within the five-worker cap. Polling is 15 seconds, cooldown is 120 seconds and HPA scale-down stabilization is separately 120 seconds; observed delays also include authentication, scheduling and processing. Service Bus/scaler identity failures are distinct from worker failures. Zero workers do not eliminate node or Premium Service Bus cost.
- **Apps CA:** Pending requested capacity triggers node allocation up to four; allocation/registration and eviction eligibility determine observed expansion/contraction. Record any capped Pending pods or no-op due to sufficient existing capacity. Quota, VM allocation, placement and disruption constraints bound success; nodes, disks and network/log traffic drive cost. Two nodes do not guarantee zone resilience.
- **Optional Spot:** eviction is a disruption event, not a fourth demand-scaling metric. Record unavailable replacement honestly; returning to regular capacity requires the reviewed selector change because no automatic fallback was configured.

For a separately approved NAP comparison, reproduce the job's requested-capacity pressure at the smaller approved bound, inspect `NodeClaim`/`NodePool` selection and consolidation, and retain provisioning/teardown evidence. Without that execution, record NAP and Automatic as design comparisons only. Neither changes the need for durable idempotency, backend headroom, external SLIs or an explicit budget.

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

Confirm the reset rather than ending at a successful Git merge:

```powershell
kubectl -n orders get hpa,scaledobject,deployments
kubectl -n orders describe scaledobject order-worker
az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName `
  --name orders --query '{active:countDetails.activeMessageCount,deadletter:countDetails.deadLetterMessageCount}' -o json
flux get kustomizations
```

Require a healthy scaler and no temporary pause; `Active=False` and zero workers are normal once active messages drain. Preserve the controlled dead-letter evidence rather than requiring a zero dead-letter count.

For **final teardown only**, remove HPA/ScaledObject/TriggerAuthentication through Git before disabling KEDA; restore intentional fixed replicas if keeping the workloads. Remove the dedicated scaler identity's `keda-operator` federation and the `scaling-identity` deployment's recorded `scalerRoleId` role assignment, then delete that scaler identity (the worker and its receiver role belong to foundation). Then run `az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --disable-keda` and follow root cleanup. Deleting a Bicep deployment record does **not** delete its resources. Persistent nodes, disks, messages and monitoring charges outlive a stopped load generator.

</details>

## Customer discovery and model answers

<details>
<summary>Model answer: Why not CPU-scale both components?</summary>

APIs often need standing latency headroom; workers need to drain backlog within an objective. Queue length/age may represent worker demand better than CPU.

</details>

<details>
<summary>Model answer: Who decides replica count?</summary>

One autoscaling owner per Deployment. Flux owns policy; HPA controls API replicas; KEDA controls worker activation and its generated HPA.

</details>

<details>
<summary>Model answer: Why are pods Pending with low node CPU?</summary>

Scheduling uses requests, placement constraints and allocatable resources; actual utilization is a different measurement.

</details>

<details>
<summary>Model answer: Does Spot plus retries guarantee correct orders?</summary>

No. At-least-once delivery requires durable idempotent business effects. Before Lab 8 this app's memory-only dedup fails after restart or across replicas.

</details>

<details>
<summary>Model answer: Does a two-minute cooldown guarantee completion?</summary>

No. Queue arrival rate, processing rate, locks, max replicas, external quota and node allocation all matter. Measure oldest-message age and business completion.

</details>

<details>
<summary>Model answer: Will NAP or Automatic always be cheaper?</summary>

Not necessarily. They alter selection/consolidation and operational work; workload shape, constraints, commitments, disruptions and continuously billable services determine total cost.

</details>

<details>
<summary>Model answer: Does the worker need Data Owner for scaling?</summary>

No. The worker remains receiver-only; a separate operator-federated scaler identity holds the queue-scoped runtime-property role. Scaling must not unnecessarily broaden application privileges.

</details>

## Optional extension

With the scaling baseline healthy and temporary faults removed, continue to lab 7 or run [Lab 11: KAITO inference](11-kaito.md) first. The KAITO exercise requires separate GPU quota/budget approval and same-session GPU cleanup; it does not replace the HPA/KEDA work or become a prerequisite for later labs.

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
