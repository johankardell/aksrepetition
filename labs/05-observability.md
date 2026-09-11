# Lab 5 — Operate against SLOs and diagnose failures

**Scenario.** Pods are Running, but customers report slow orders. Build evidence that separates HTTP acceptance, asynchronous completion, platform health and monitoring failure.

**Prerequisites/re-entry.** Complete Lab 4 with `orders` and `orders-test` Flux Kustomizations Ready. Run PowerShell 7 at the root on private management connectivity. Re-read local configuration and the monitoring deployment outputs when returning. The platform operator needs AKS configuration/diagnostic permissions and role-assignment rights; the observer needs Monitoring Reader, Log Analytics Reader and Grafana Viewer at their respective resources. Deploying a Grafana resource does not by itself give dashboard access.

**Paths and scope.** API `/metrics` → managed `ama-metrics` agent → **Azure Monitor workspace** (Prometheus); container stdout/stderr + selected control-plane diagnostics → existing **Log Analytics workspace**; application Azure Monitor OpenTelemetry distro → workspace-based **Application Insights**; Grafana queries Prometheus using its own managed identity. An Azure Monitor workspace is **not** a Log Analytics workspace. The worker is not an HTTP metrics server; observe its processing through logs/traces and Service Bus metrics, not a nonexistent worker `/metrics`.

**Resource inventory and cost warning.** Add Azure Monitor workspace, **Managed Grafana Standard** (billable continuously), Application Insights, action group and Prometheus alert rules. Reuse the existing logs workspace and monitoring add-on. Prometheus samples/query volume, logs/trace ingestion, retention, alert evaluation and emails can incur cost. The small lab uses authenticated **public telemetry/query endpoints over TLS with Firewall allowlisting**, not AMPLS or private Grafana. This does **not** expose the AKS API or ACR. A policy requiring private-only observability needs the documented AMPLS/DCE and Grafana private-endpoint design first; do not silently open otherwise-prohibited public telemetry.

**Lab SLO contract.** For the synthetic exercise: ≥99% successful user-path HTTP responses and p95 ≤1 second over a measured five-minute interval; orders accepted for processing should complete within 60 seconds at baseline load. These are lab objectives, not Microsoft's AKS SLA or a claimed production 30-day SLO. Admission 202 is not completion. Define production SLIs, exclusions and a longer window with the business before setting alerts.

## Numbered directives

### 1. Verify providers, diagnostics and network prerequisites

**Challenge:** verify the required resource providers, supported regional destinations, existing Container Insights authentication and diagnostic categories before onboarding telemetry.

**Constraints:** stop on an unsupported/preview-only regional combination. Check Lab 3 Firewall desired state permits TCP 443 to `*.monitor.azure.com`, `*.monitoring.azure.com`, `*.ods.opinsights.azure.com`, `*.oms.opinsights.azure.com`, `login.microsoftonline.com`, `global.handler.control.monitor.azure.com`, and the exact Application Insights ingestion hostname. Preserve private Service Bus/ACR routes. TLS inspection and proxies need a separate supported design; do not assume a generic HTTPS proxy works with private AKS.

**Exit evidence:** record supported locations, Container Insights managed-identity authentication, the four selected diagnostic categories and approved telemetry egress.

<details>
<summary>Solution</summary>

```powershell
. .\scripts\Use-Lab.ps1
flux get kustomizations
foreach ($provider in 'Microsoft.Monitor','Microsoft.Dashboard','Microsoft.Insights','Microsoft.AlertsManagement') {
    az provider register --namespace $provider --wait
}
az provider show --namespace Microsoft.Dashboard --query "resourceTypes[?resourceType=='grafana'].locations" -o json
az provider show --namespace Microsoft.Monitor --query "resourceTypes[?resourceType=='accounts'].locations" -o json
az monitor diagnostic-settings categories list --resource $Outputs.clusterId.value -o table
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query addonProfiles.omsagent -o json
```

Compare returned locations with `$Lab.Location` and the existing Log Analytics workspace location/support. The selected categories in `ops\monitoring.bicep` are `kube-audit-admin`, `kube-apiserver`, `kube-controller-manager` and `kube-scheduler`; each must appear in the cluster's category list. Check the existing `omsagent` profile is enabled and configured for managed-identity authentication.

`*.monitor.azure.com` covers regional handler and metrics ingestion endpoints in this sample. Compare the deployed Firewall policy with the approved desired state, not only DNS resolution: a resolvable endpoint can still be blocked on TCP 443.

</details>

### 2. Deploy the telemetry destinations and connect managed Prometheus

**Challenge:** provision the telemetry resources and approved alert recipient, connect the AKS managed Prometheus add-on, and verify Grafana integration and agent readiness.

**Ownership/constraints:** `monitoring.bicep` owns destinations, roles, alerts and diagnostics; CLI onboarding owns AKS add-on settings and generated DCR/DCE associations. Do not redeploy older foundation intent that resets the add-on profile. Use the supported service-default Grafana version and update lifecycle, not an obsolete major or preview integration CLI. Authenticated public telemetry must already be approved under this lab's network/cost contract.

**Exit evidence:** record resource outputs, Grafana version/endpoint and scoped reader/viewer assignments, Ready metrics agents and the managed PodMonitor CRD.

<details>
<summary>Solution</summary>

```powershell
$AlertEmail = Read-Host 'Approved recipient for synthetic lab alerts'
az deployment group create -g $Lab.ResourceGroup -n monitoring `
  --template-file .\ops\monitoring.bicep `
  --parameters prefix=$Lab.Prefix location=$Lab.Location clusterName=$Lab.ClusterName `
    workspaceId=$Outputs.workspaceId.value grafanaName=$Lab.GrafanaName `
    viewerObjectId=$Lab.AdminGroupObjectId viewerPrincipalType=Group alertEmail=$AlertEmail
$Monitor = az deployment group show -g $Lab.ResourceGroup -n monitoring --query properties.outputs -o json | ConvertFrom-Json
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --enable-azure-monitor-metrics `
  --azure-monitor-workspace-resource-id $Monitor.metricsId.value
kubectl -n kube-system get pods -o wide | Select-String 'ama-metrics|ama-logs'
kubectl get crd podmonitors.azmonitoring.coreos.com
az rest --method get --url "$($Monitor.grafanaId.value)?api-version=2023-09-01" `
  --query '{version:properties.grafanaVersion,endpoint:properties.endpoint,identity:identity.principalId}'
```

The template links Grafana to the workspace and assigns its identity **Monitoring Reader** at that workspace. This avoids the preview-tagged `az grafana integration monitor add` CLI command. The service chooses its currently supported default Grafana version; record the returned version and follow Grafana's supported update lifecycle rather than pinning an obsolete major. CLI onboarding owns AKS monitoring add-on settings and its generated DCR/DCE association; `monitoring.bicep` owns the destinations/roles/alerts/diagnostics. Do not repeatedly redeploy an older foundation that resets the add-on profile; reconcile its infrastructure intent before future platform redeployments.

**Expected evidence:** `ama-metrics` pods Ready and the managed PodMonitor CRD present. ACR private endpoints do not provide Azure Monitor connectivity. `401` ingestion suggests onboarding/identity, DNS timeout suggests routing/DNS, and a valid add-on with no custom series often means the wrong CRD API group.

</details>

### 3. Wire real application telemetry and scraping through GitOps

**Challenge:** enable the image's existing telemetry instrumentation and API scraping through a reviewed GitOps change, without committing the ingestion connection string or adding duplicate instrumentation.

Application Insights connection strings identify an ingestion resource; they are not an Azure authorization credential. Still keep this operational value out of Git and logs. Read it in memory and create a Kubernetes Secret as an explicitly separate platform-owned object. Do not print or persist the rendered Secret.

**Constraints:** the PodMonitor/NetworkPolicy trust kube-system monitoring agents, not arbitrary workload namespaces. Do not enable automatic instrumentation over the already instrumented image. The worker has no HTTP metrics endpoint.

**Exit evidence:** retain the reviewed telemetry diff, both rollout results, managed PodMonitor details and initialization logs without secret values; verify the authenticated Grafana endpoint is accessible with scoped permissions.

<details>
<summary>Solution</summary>

```powershell
$insights = az rest --method get --url "$($Monitor.appInsightsId.value)?api-version=2020-02-02" | ConvertFrom-Json
$connection = $insights.properties.ConnectionString
if (-not $connection) { throw 'Application Insights connection string was not returned.' }
try {
    kubectl -n orders create secret generic orders-telemetry `
      --from-literal="connection-string=$connection" --dry-run=client -o yaml | kubectl apply -f -
} finally {
    $connection = $null
    $insights = $null
}
.\ops\Add-GitOpsFile.ps1 -Source .\ops\monitoring\podmonitor.yaml
.\ops\Add-GitOpsFile.ps1 -Source .\ops\monitoring\telemetry-patch.yaml -Kind Patch
kubectl kustomize .\gitops\clusters\primary\apps\orders
git switch -c enable-orders-telemetry
git add gitops
git commit -m "Scrape orders and enable correlated application telemetry"
git push -u origin HEAD
gh pr create --base main --fill
```

After review/merge:

```powershell
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
kubectl -n orders rollout status deployment/order-api --timeout=300s
kubectl -n orders rollout status deployment/order-worker --timeout=300s
kubectl -n orders get podmonitor.azmonitoring.coreos.com
kubectl -n orders logs deployment/order-api --tail=40
$Monitor.grafanaEndpoint.value
```

**Evidence:** the image's existing `configure_azure_monitor()` executes when the new environment variable is present; both service roles now export spans. We do not enable automatic instrumentation on top of the already instrumented image (which would risk duplicate telemetry). The API PodMonitor uses **`azmonitoring.coreos.com/v1`**, not the OSS `monitoring.coreos.com/v1` group. It scrapes named port `http` every 30 seconds. Its companion NetworkPolicy allows kube-system agents to reach API port 8080; this trusts kube-system, not arbitrary workload namespaces. Tighten pod selectors after inspecting your actual managed-agent labels if needed.

</details>

### 4. Follow one synthetic order end to end

**Challenge:** send a uniquely identified synthetic order, trace enqueue through worker processing, and correlate application telemetry with container logs and API metrics. Capture a local baseline without mistaking it for the user-path SLO.

**Constraints:** use only a management-local port-forward and synthetic payloads. Allow ingestion time, and keep 100% trace sampling until this proof is complete. Do not claim durable fulfillment before Lab 8 or full client-to-worker tracing without validating the actual instrumentation.

**Exit evidence:** retain order/trace IDs, correlated API/worker spans and timestamps, the worker log, metrics sample and baseline artifact. Evaluate the 60-second processing objective separately from HTTP acceptance.

<details>
<summary>Solution</summary>

Start a management-only port-forward in a second terminal:

```powershell
kubectl -n orders port-forward service/order-api 8081:80
```

Then:

```powershell
$OrderId = "trace-$([guid]::NewGuid().ToString('N'))"
$TraceId = [guid]::NewGuid().ToString('N')
$SpanId = [guid]::NewGuid().ToString('N').Substring(0,16)
Invoke-RestMethod http://127.0.0.1:8081/orders -Method Post -ContentType application/json `
  -Headers @{ traceparent = "00-$TraceId-$SpanId-01" } `
  -Body (@{ id=$OrderId; item='synthetic-trace' } | ConvertTo-Json -Compress)
kubectl -n orders logs deployment/order-worker --since=5m | Select-String $OrderId
(Invoke-WebRequest http://127.0.0.1:8081/metrics).Content | Select-String 'orders_http'
.\ops\Invoke-OrderLoad.ps1 -BaseUri http://127.0.0.1:8081 -Count 60 -Concurrency 2 -OutputPath .artifacts\baseline.json
```

Allow several minutes for ingestion, then query the existing workspace:

```powershell
.\ops\Invoke-LogsQuery.ps1 -Query @"
union isfuzzy=true AppRequests, AppDependencies, AppTraces
| where OperationId == '$TraceId'
| project TimeGenerated, Type, AppRoleName, OperationId, ParentId,
          Name=column_ifexists('Name',''), Message=column_ifexists('Message','')
| order by TimeGenerated asc
"@
.\ops\Invoke-LogsQuery.ps1 -Query @"
ContainerLogV2
| where PodNamespace == 'orders' and LogMessage has '$OrderId'
| project TimeGenerated, PodName, LogMessage
"@
```

**Evidence:** the API's `orders.enqueue` and worker's `orders.process` share an OperationId through Service Bus application properties; the parent span can be traced across asynchronous processing. The worker log identifies the synthetic order and reports `durable:false` before Lab 8. Missing shared IDs suggest dropped application properties, a missing OTel initializer or sampling, not necessarily Service Bus failure. If automatic HTTP instrumentation differs in the installed distro, first correlate the explicitly created enqueue/process spans; verify FastAPI auto-instrumentation before claiming client-to-worker trace continuity.

Use span timestamps/durations and the matching processed log to compare enqueue with processing completion. A measured interval within 60 seconds satisfies only this synthetic processing check; before Lab 8 there is no durable business row. Missing telemetry is an evidence gap, not proof that processing failed or that the objective was met. The port-forward bypasses gateway/TLS routing and cannot certify the external user path.

</details>

### 5. Compare user SLIs with Prometheus and create a useful alert evidence trail

**Challenge:** compare five-minute API availability/latency and queue metrics with measured consumer-path HTTPS results. Explain blind spots, then collect genuine alert firing, notification and recovery evidence during directive 8.

**Constraints:** use authenticated Grafana with Viewer for queries; saving dashboards requires scoped Editor, never global admin. Test from the consumer network with the real trusted Lab 3 HTTPS URL, without disabling certificate verification. Missing data is not proof of availability, and deployed rules are not proof of notification.

**Exit evidence:** retain both query results and timed local/external load artifacts, explain any discrepancy, and record rule name, affected SLI, fired/resolved UTC times and received email from an actual incident. Do not claim a production SLO from these lab thresholds.

<details>
<summary>Solution</summary>

Open the authenticated Grafana endpoint printed in directive 3. In **Explore**, select the provisioned Azure Monitor workspace Prometheus data source. Run these exact PromQL expressions (copying via PowerShell is convenient on Windows):

```powershell
$AvailabilityQuery = @'
1 - (sum(rate(orders_http_requests_total{namespace="orders",status=~"5.."}[5m])) or vector(0))
    / clamp_min(sum(rate(orders_http_requests_total{namespace="orders"}[5m])), 0.001)
'@
$LatencyQuery = @'
histogram_quantile(0.95, sum by (le) (rate(orders_http_duration_seconds_bucket{namespace="orders"}[5m])))
'@
$AvailabilityQuery
$LatencyQuery
az monitor metrics list --resource $Outputs.serviceBusId.value `
  --metric ActiveMessages DeadletteredMessages --interval PT1M --aggregation Average `
  --filter "EntityName eq 'orders'" -o json
```

**Evidence:** no result means “no series,” not 100% availability. With no failures, the `or vector(0)` branch avoids an empty error numerator. The alert uses 5xx ratio, p95 and a separate missing-metrics rule; action-group email requires a **real firing condition**, not merely a deployed rule. The availability query sees only requests reaching the API. A gateway outage or no Ready endpoints may never increment it. Test the real Lab 3 HTTPS URL from the consumer network with `Invoke-OrderLoad.ps1` and compare its measured availability/p95 to the port-forward baseline; trust the external measurement for user impact. Do not disable TLS certificate verification.

Use Grafana Editor only if you need to save a dashboard; Viewer can query but cannot author. Ask the platform operator for a scoped role, not global admin. In Azure Monitor Alerts, record rule name, fired/resolved UTC time, affected SLI, and received email. For a production SLO, add gateway/external-probe telemetry and multi-window error-budget burn rules; these five-minute lab thresholds are deliberately simpler.

With directive 4's port-forward still running, capture a five-minute local interval. Then run the HTTPS measurement from the consumer-connected host with the repository scripts available:

```powershell
.\ops\Invoke-OrderLoad.ps1 -BaseUri http://127.0.0.1:8081 -Operation Browse `
  -Count 2000 -Concurrency 1 -DurationSeconds 300 -DelayMilliseconds 200 `
  -OutputPath .artifacts\slo-local.json
$UserUri = (Read-Host 'Trusted HTTPS application URL from lab 3').TrimEnd('/')
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Operation Browse `
  -Count 2000 -Concurrency 1 -DurationSeconds 300 -DelayMilliseconds 200 `
  -OutputPath .artifacts\slo-external.json
Get-Content .artifacts\slo-external.json -Raw | ConvertFrom-Json |
  Select-Object startedUtc,elapsedSeconds,sent,successful,availabilityPercent,p95Milliseconds
```

If using a separate consumer host, retain its output there and carry only the synthetic measurement artifact into the execution record. The count/rate combination keeps request starts available throughout the 300-second window; in-flight requests can finish later. Query Grafana for each run's corresponding interval, not a different idle period.

Compare the external `availabilityPercent` with 99 and `p95Milliseconds` with 1000. The load tool counts HTTP 2xx/3xx as successful and includes transport failures as status 0; the Prometheus expression measures server-side 5xx only, across traffic reaching the API. Client p95 includes network/gateway time and timeouts, whereas the histogram estimates server latency. Different populations and sequential time windows can therefore differ legitimately. Use the trace from directive 4 to evaluate processing delay separately.

The configured rules are `OrdersHighErrorRatio`, `OrdersHighLatency` and `OrdersMetricsMissing`, evaluated every minute with a two-minute hold. In directive 8 compare the actual failing-request window with rule history and the action-group email; then wait for resolution after recovery. If a rule fires without email, inspect action-group recipient/delivery configuration rather than reporting successful notification.

</details>

### 6. Inject CPU pressure and distinguish saturation from dependency latency

**Challenge:** apply bounded CPU pressure to one API container while measuring real HTTPS traffic. Decide whether any latency is associated with container saturation or backend dependency time using metrics and traces.

**Constraints:** use a pod-specific unprivileged 90-second process and the bounded load below, not a persistent stress Deployment. Spare replicas may preserve the SLO; report resilience honestly rather than forcing a breach.

**Exit evidence:** retain the affected pod, CPU observations, load interval/availability/p95 and matching traces. Explain the distinction between container and node headroom and compare with directive 8's dependency incident.

<details>
<summary>Solution</summary>

Run the following in a second terminal while generating a bounded `Browse` load against the **real HTTPS application URL** in the first. Use a pod-specific exec process, not a privileged stress image:

```powershell
. .\scripts\Use-Lab.ps1
$ApiPod = kubectl -n orders get pods -l app=order-api -o 'jsonpath={.items[0].metadata.name}'
kubectl -n orders exec $ApiPod -c api -- python -c `
  "import time; end=time.monotonic()+90; exec('while time.monotonic()<end:\n sum(range(10000))')"
```

```powershell
$UserUri = (Read-Host 'Trusted HTTPS application URL from lab 3').TrimEnd('/')
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Operation Browse -Count 600 `
  -Concurrency 4 -DurationSeconds 120 -DelayMilliseconds 300 -OutputPath .artifacts\cpu-impact.json
kubectl -n orders top pods --containers
kubectl top nodes
```

**Expected:** the selected API container approaches its CPU limit during the 90-second injection; impact depends on spare capacity and how traffic distributes. It is valid for the external SLO to remain healthy because two replicas isolate the disturbance. Do not invent a breached p95 when measurements show resilience. The exec process exits itself; there is no persistent CPU stress Deployment.

Sample `kubectl top pods --containers` from the first terminal while the load is still running, as the post-load sample may already show recovery. Compare the hot container with its Git-owned CPU request/limit and with node CPU. Low node CPU does not exclude a container hitting its own limit. `kubectl top` alone does not prove throttling; use throttling counters if collected.

Browse does not enqueue an order, so a slowdown aligned with the injected CPU process is not itself evidence of Service Bus latency. For the dependency exercise, use the enqueue span/dependency errors and client failures alongside normal CPU. Re-query directive 4's trace/log sources for the relevant run's IDs and times rather than inferring causality from one graph.

</details>

### 7. Inject a bad readiness probe without allowing Flux to instantly repair it

**Challenge:** inject an incorrect readiness path under the platform incident role, observe rollout/endpoints and user impact, then restore Git desired state and prove recovery.

**Constraints:** suspend only the application child, not platform reconciliation; always resume it. Do not delete healthy replicas to manufacture an outage. If using the alternate `FAIL_READINESS` injection, remove its unmanaged live override before resuming.

**Exit evidence:** record pod readiness, endpoint membership, probe events, actual external SLIs and the recovered revision/rollout. Distinguish release health from user availability.

<details>
<summary>Solution</summary>

Use the platform incident role and preserve the source of truth:

```powershell
flux suspend kustomization orders
try {
    kubectl -n orders patch deployment order-api --type=json `
      -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/deliberately-missing"}]'
    Start-Sleep -Seconds 40
    kubectl -n orders get pods -l app=order-api
    kubectl -n orders get endpointslices -l kubernetes.io/service-name=order-api
    kubectl -n orders get events --sort-by=.lastTimestamp
    .\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Operation Browse -Count 20 `
      -Concurrency 1 -OutputPath .artifacts\readiness-impact.json
} finally {
    flux resume kustomization orders
    flux reconcile kustomization orders --with-source
}
kubectl -n orders rollout status deployment/order-api --timeout=300s
```

**Diagnosis:** new pods can be Running but not Ready; events show HTTP 404 probes. A rolling Deployment normally preserves old Ready replicas, so the rollout may stall **without a complete outage**. Endpoints and actual user measurements decide impact. The monitoring agent may still scrape unready pods and produce good-looking process metrics. Git's valid `/readyz` probe returns through reconciliation; verify the new revision and Ready endpoints.

The application also supports `FAIL_READINESS=true`, which makes the **valid** `/readyz` endpoint return 503. That is an application-declared readiness failure, distinct from this intentionally incorrect probe path returning 404. If using that alternate injection, explicitly remove the added environment entry with `kubectl -n orders set env deployment/order-api FAIL_READINESS-` before resuming Flux; do not leave an unmanaged live override.

</details>

### 8. Inject a dependency failure and prove a 5xx alert

**Challenge:** inject a reversible Service Bus dependency fault, distinguish its symptoms from CPU pressure, and verify a real alert and recovered user path.

**Constraints:** use a nonexistent queue, never delete the real queue/messages or alter working host/credentials/network. Keep the run bounded to the stated load limits. Do not grant Data Owner to fix this injection. Remove the live environment override before resuming the application child, including after interruption; SDK retries may outlive clients.

**Exit evidence:** retain user-path results, CPU and dependency errors, scrape health, actual fired/resolved alert history/email and successful recovery. If timeouts prevent a 5xx rule firing, record the gap and observed latency alert rather than asserting a pass.

<details>
<summary>Solution</summary>

Use a deliberately nonexistent Service Bus queue rather than deleting the real queue or its messages. API credential/host/network remain intact:

```powershell
flux suspend kustomization orders
try {
    kubectl -n orders set env deployment/order-api QUEUE_NAME=orders-intentionally-absent
    kubectl -n orders rollout status deployment/order-api --timeout=300s
    .\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Count 1000 -Concurrency 2 `
      -DurationSeconds 480 -DelayMilliseconds 1000 -TimeoutSeconds 30 `
      -OutputPath .artifacts\dependency-impact.json
    kubectl -n orders logs deployment/order-api --since=10m --tail=100
    kubectl -n orders top pods
} finally {
    # Remove the extra live env entry; it was not present in Git's envFrom-based configuration.
    kubectl -n orders set env deployment/order-api QUEUE_NAME-
    flux resume kustomization orders
    flux reconcile kustomization orders --with-source
}
kubectl -n orders rollout status deployment/order-api --timeout=300s
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Count 20 -Concurrency 1 -OutputPath .artifacts\recovered.json
```

**Evidence:** backend authorization/entity-not-found errors in traces/logs, failed or timed-out user requests, and low-to-normal CPU distinguish this from directive 6. A missing queue can produce authorization-style errors because the identity's role is scoped to the existing queue. Do not “fix” it by granting Service Bus Data Owner. The eight-minute bounded run is long enough for a five-minute evaluation plus two-minute hold when 5xx reach the application metrics; retain alert email and fired/resolved history. If client timeouts occur before the server emits 5xx, use user-path evidence and dependency traces; do not claim the Prometheus error rule fired without evidence. Retry evaluation after requests finish, verify scrape health, and compare the latency rule. The `finally` block explicitly removes the injected env entry before resuming Git desired state, because server-side apply may preserve a newly added field owned by another manager. SDK retries can outlive individual clients.

</details>

### 9. Reduce collection volume deliberately and hand over

**Challenge:** reduce log collection/retention and both services' trace sampling deliberately, verify the applied configuration and measured ingestion, and hand over the retained telemetry stack.

**Ownership/constraints:** the platform owns the managed-agent ConfigMap outside application Flux; this configuration targets Standard clusters, not Automatic managed-system-node behavior. Review security/incident needs before excluding logs; retain selected control-plane audit diagnostics. Keep 100% traces until correlation proof, change both sampling values through review, and do not sample Prometheus SLIs or add unbounded/sensitive metric labels.

**Exit evidence:** retain accepted agent-configuration evidence, table retention/ingestion results, reviewed sampling diff and observed `ItemCount` values, plus cleanup ownership and continuing costs.

Retain telemetry resources for labs 6–10 and stop port-forwards when done. Final teardown is only at course end; do not delete the shared Log Analytics workspace mid-course. Retention and Grafana still bill when AKS is stopped; ingestion caps and budget emails are not guaranteed spending caps.

<details>
<summary>Solution</summary>

The platform operator owns the managed-agent ConfigMap outside the application Kustomization:

```powershell
kubectl apply -f .\ops\monitoring\container-logs.yaml
kubectl -n kube-system get pods | Select-String 'ama-logs'
az monitor log-analytics workspace table update -g $Lab.ResourceGroup `
  --workspace-name $Lab.WorkspaceName --name ContainerLogV2 --retention-time 30 --total-retention-time 30
.\ops\Invoke-LogsQuery.ps1 -Query @'
Usage
| where TimeGenerated > ago(24h) and IsBillable == true
| summarize IngestedMB=sum(Quantity) by DataType
| order by IngestedMB desc
'@
```

**Evidence:** log-agent configuration has no parse errors; `KubeMonAgentEvents` reports accepted config after its reporting interval. This ConfigMap is for **Standard** clusters; Automatic with managed system node pools has different support. We exclude kube-system/gatekeeper stdout/stderr and disable environment-variable collection; that does not disable selected control-plane audit diagnostics. Review the security/incident trade-off before excluding logs.

For ongoing operation change **both** `OTEL_TRACES_SAMPLER_ARG` values in the Git-owned `telemetry-patch.yaml` from `"1.0"` to `"0.1"` through a PR, merge and reconcile. Keep 100% while proving cross-service traces. Sampling is not applied to the Prometheus SLIs. Inspect `ItemCount` in Application Insights tables; don't infer actual sampling from manifest text alone. Never put order IDs, emails or raw URLs in metric labels; current labels have bounded method/status cardinality.

```powershell
git switch -c reduce-trace-sampling
$patchPath = '.\gitops\clusters\primary\apps\orders\telemetry-patch.yaml'
$patch = (Get-Content $patchPath -Raw).Replace('value: "1.0"', 'value: "0.1"')
Set-Content $patchPath $patch -Encoding utf8
git add gitops
git commit -m "Reduce trace sampling after diagnostic proof"
git push -u origin HEAD
gh pr create --base main --fill
# After review/merge:
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
```

After the agents' reporting/ingestion interval, inspect configuration events and verify sampling using newly generated traffic:

```powershell
.\ops\Invoke-LogsQuery.ps1 -Query @'
KubeMonAgentEvents
| where TimeGenerated > ago(1h)
| project TimeGenerated, Computer, Severity, Message
| order by TimeGenerated desc
'@
.\ops\Invoke-OrderLoad.ps1 -BaseUri $UserUri -Count 100 -Concurrency 2 `
  -OutputPath .artifacts\sampling-check.json
.\ops\Invoke-LogsQuery.ps1 -Query @'
union isfuzzy=true AppRequests, AppDependencies, AppTraces
| where TimeGenerated > ago(30m)
| where AppRoleName in ('order-api', 'order-worker')
| summarize StoredRows=count(), RepresentedItems=sum(ItemCount) by AppRoleName, ItemCount
'@
```

Wait for ingestion and narrow the time filter to after the sampling rollout when interpreting the result. Weighted `ItemCount` values can demonstrate sampling; a low-volume run with no sampled rows is inconclusive, not proof of failed processing. Compare repeated billable `Usage` windows before/after the change, accounting for traffic and ingestion delay, rather than promising a fixed cost reduction.

Retain Prometheus/Grafana/Logs/Application Insights for labs 6–10. Stop port-forwards with Ctrl+C. Final cleanup: remove the telemetry patch/PodMonitor through Git, reconcile, delete the `orders-telemetry` Secret, disable the metrics add-on with `az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --disable-azure-monitor-metrics`, and use root resource cleanup for telemetry resources. Do not delete the shared Log Analytics workspace mid-course. Workspace retention and Grafana billing remain even when AKS is stopped; daily ingestion caps/budget emails are not guaranteed spending caps.

</details>

## Customer discovery and model answers

<details>
<summary>Model answer: Which SLI represents “order success”?</summary>

Separate API acceptance from durable business completion and queue age. A 202 and an empty CPU graph do not prove fulfillment. Lab 8 adds durable lookup/idempotency.

</details>

<details>
<summary>Model answer: Metrics, logs or traces?</summary>

Metrics quantify trends and alert; logs explain events; traces connect causal operations. Use one correlation ID across all, but never as a metric label.

</details>

<details>
<summary>Model answer: Why not collect everything forever?</summary>

Ingestion, retention and cardinality cost money and can bury relevant evidence. Preserve mandatory audit evidence while testing deliberate filtering/sampling.

</details>

<details>
<summary>Model answer: Is a private cluster automatically private monitoring?</summary>

No. API access, ingestion, query access, DNS and Grafana's browser endpoint are different paths. This lab declares authenticated public telemetry with controlled egress explicitly.

</details>

<details>
<summary>Model answer: Why did the bad probe not violate availability?</summary>

Rolling-update behavior retained healthy replicas. A stalled rollout is a release incident, not automatically a user outage. Verify external SLIs.

</details>

<details>
<summary>Model answer: Can an alert rule prove an SLO?</summary>

It detects a chosen symptom over a window. The SLO needs a defined population, time window, exclusions and user-impact signal, including requests that never reached pods.

</details>

## References and support

Source review: **2026-09-10**. Managed Prometheus, Managed Grafana Standard, Container Insights and SDK-based Azure Monitor OTel are the required supported path. Kubernetes monitoring CRDs use the managed Azure API group. `diagnosticSettings@2021-05-01-preview` is the published ARM schema for the **GA diagnostic-settings service**, not a dependency on a preview AKS feature. Live Azure ingestion, alert delivery and regional provisioning remain execution checks, not authoring claims.

- [Enable AKS monitoring](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/kubernetes-monitoring-enable)
- [Managed Prometheus PodMonitor support](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/prometheus-metrics-scrape-crd)
- [Monitoring firewall endpoints](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/kubernetes-monitoring-firewall)
- [Container log filtering](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/kubernetes-data-collection-configmap)
- [OTel configuration and sampling](https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-configuration)
- [Prometheus alert rule schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.alertsmanagement/prometheusrulegroups)
- [Grafana roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/monitor)
