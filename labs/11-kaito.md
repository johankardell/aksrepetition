# Lab 11 - Serve a private model with KAITO and account for its GPU lifecycle

**Customer:** an engineering team wants self-hosted inference for a synthetic order-support assistant, without turning an unauthenticated model endpoint into a public service. **Time:** 2-4 hours, including provisioning and cleanup. **Result:** a managed KAITO Workspace, verified GPU placement, a measured OpenAI-compatible request, a real network-denial/recovery exercise, and evidence that GPU compute was removed.

**Prerequisites/re-entry.** Optional after labs 1-6, before final teardown. Keep their private management path, Cilium Overlay/UDR network, private ACR and healthy orders GitOps. Labs 7-10 are not prerequisites; if already completed, retain their governance, upgraded versions and restored primary-region state. Run from the repository root in Linux Bash with `jq`, `curl`, and Python 3 on the private management host. The operator needs AKS update and node-pool deletion authority plus Kubernetes platform administration; the image publisher and orders Flux service account must not receive these rights.

The sourced `./scripts/use-lab.sh` helper supplies JSON strings `Lab` and `Outputs`, absolute repository path `Root`, and the strict `jq -er` accessors `lab_value KEY` and `output_value KEY`. Keep the original shell open between tasks, and never enable shell tracing or record transcripts while handling authentication material.

**Support gate.** Source review: **2026-10-01**. This exercise reviews **KAITO 0.6.0** and uses that release's `kaito.sh/v1beta1` Workspace and `phi-4-mini-instruct` preset, not an example downloaded from `main`. Microsoft's managed AI toolchain operator guide warns that the add-on typically lags upstream; it does not pin the deployed managed version to 0.6.0. Verify the actual controller image/version and supported AKS/region/SKU combination before submitting the Workspace. Public Azure regions and NVIDIA Linux GPU nodes are the path here; Windows and AMD GPU workspaces are excluded. Do not install an upstream KAITO Helm release, a second GPU operator, NAP or preview extensions over the managed add-on to repair a compatibility problem. The separate **fully managed GPU node-pool feature is preview** in the reviewed documentation and is not required here.

**Envelope and data boundary.** One Workspace, one desired `Standard_NC24ads_A100_v4` node (24 vCPUs, one A100), one model replica, one CPU-only probe, five sequential measured requests, at most 64 generated tokens per request, and a four-hour allocation window including teardown. Approve current regional GPU pricing, NCads A100 v4 family quota, total regional quota, disk space and subnet capacity first. Existing orders infrastructure continues billing. `resource.count: 1` is desired capacity, **not an Azure spending cap or a guarantee against transient/orphaned allocations**. The namespace's one-GPU quota limits pod requests, not Azure node creation. Monitor pool count during reconciliation; stop and clean up if the footprint exceeds approval. Budgets are notifications, and even quota is not a monetary cap.

Use only the supplied synthetic prompts. Do not send customer orders, credentials or personal information. Model weights and runtime artifacts are downloaded; review their license, provenance and vulnerability status. Self-hosting does not supply content filtering, prompt-injection protection, end-user authentication or permission to use a model. No tool execution, RAG ingestion, training, fine-tuning or orders-application integration is performed.

**Architecture/ownership.** The platform enables the managed add-on; KAITO reconciles the Workspace into GPU capacity and an inference Deployment/ClusterIP Service. The platform owns the temporary `kaito-lab` resources imperatively, like the incident namespace in lab 9; they are deliberately outside application Flux. KAITO owns its generated objects, so do not add HPA/KEDA or manually scale/edit that Deployment. Existing orders autoscalers and `system`/`apps` pools remain unchanged. For a persistent offering, promote the Workspace and policies through a separately authorized platform GitOps path, not the orders application's permissions.

## 1. Make a go/no-go decision before enabling anything

**Challenge:** prove you are managing the primary lab cluster, establish a persisted pre-change inventory, and approve the GPU/version/network envelope. Treat unavailable capacity as a blocked exercise, not a reason to enlarge the SKU or expose the API.

**Exit evidence:** cluster/context, managed-version compatibility decision, baseline pool names, quota/SKU restrictions, cost owner and cleanup deadline. If quota or support is unavailable, record **not executed**; a design comparison is not a successful deployment.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
umask 077
source ./scripts/use-lab.sh
KaitoDir="$Root/.artifacts/kaito"
if [[ -e "$KaitoDir" ]]; then
  printf 'Existing KAITO evidence: use the re-entry instructions; do not overwrite the cleanup baseline.\n' >&2
  exit 1
fi
mkdir -p "$KaitoDir"
az aks get-credentials -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --overwrite-existing
kubelogin convert-kubeconfig -l azurecli
kubectl config current-context
kubectl get nodes -o wide
flux get kustomizations -A
kubectl -n orders get deployments,hpa,scaledobjects

az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o json > "$KaitoDir/cluster-before.json"
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -o json > "$KaitoDir/pools-before.json"
az vm list-usage -l "$(lab_value Location)" -o json > "$KaitoDir/quota-before.json"
az vm list-skus -l "$(lab_value Location)" --size Standard_NC24ads_A100_v4 --all -o json > "$KaitoDir/sku.json"
cat "$KaitoDir/quota-before.json" "$KaitoDir/sku.json"
az aks update --help | grep 'ai-toolchain'
# No matches is valid for a pre-install inventory; kubectl errors still stop the shell.
Crds=$(kubectl get crd -o name)
if ! grep -E 'kaito|karpenter' <<< "$Crds"; then printf 'No KAITO/Karpenter CRDs found.\n'; fi
Controllers=$(kubectl get deployments,daemonsets -A)
if ! grep -E 'kaito|gpu|nvidia' <<< "$Controllers"; then printf 'No matching GPU controllers found.\n'; fi
```

Inspect `cluster-before.json`: private API, correct resource ID/location, OIDC enabled, Workload ID enabled, Cilium Overlay and `userDefinedRouting` preserved. Compare unused **both** regional and NCads A100 v4-family vCPUs with the 24-vCPU node; `list-skus` must show no applicable subscription/location restriction. Quota does not guarantee allocation or zone capacity. Do not reuse an unrelated existing GPU pool simply because it fits.

Check the live managed add-on support guidance, API availability and existing operators. If KAITO is already installed, inventory `kubectl get workspaces.kaito.sh -A` and obtain its platform owner's agreement before continuing; never replace another installation. If another Workspace or concurrent GPU-provisioning exercise exists, stop rather than assuming this lab's ownership/cleanup boundaries apply.

Review the Lab 3 firewall's current rules and DNS. This preset needs MCR runtime/model artifacts and their documented data endpoints (including `mcr.microsoft.com` and `*.data.mcr.microsoft.com`); inspect the running add-on's downloader image too. Azure control-plane/identity and AKS bootstrap endpoints must remain reachable. Private ACR connectivity alone is insufficient for public model artifacts. The supplied pod policy permits public TCP 443, with the **existing Azure Firewall FQDN rules** providing the destination allowlist. It does not permit access to private orders dependencies. Use firewall deny logs to identify missing artifact endpoints and amend the owned rules narrowly; never replace the accumulated firewall configuration with the original bootstrap or allow unrestricted Internet egress. No Hugging Face token is needed for this public preset.

Record the approved start/deadline, SKU, expected node count, current pricing and responsible cleanup owner in the local execution record. Re-entry after an interrupted attempt starts by loading these baseline files, checking active Workspaces/pools and completing task 7 if time has expired. Do not recapture the baseline after provisioning: it would hide newly created pools.

</details>

## 2. Enable the managed operator and establish the isolated namespace

**Challenge:** enable only the supported AKS add-on, verify controller/API health, and install namespace guardrails before submitting a billable Workspace.

**Constraints:** no subscription-wide Contributor workaround or manual edits to managed controllers. Baseline PSA is an explicit, namespace-local exception for the generated preset pod, which does not meet restricted PSA by default; restricted warnings/audit remain enabled. Do not weaken any existing team's policy.

**Exit evidence:** add-on profile, controller images/readiness, served CRD version, namespace policies and quota. API acceptance alone is not model readiness.

<details>
<summary>Solution</summary>

```bash
az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --enable-ai-toolchain-operator --enable-oidc-issuer
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o json > "$KaitoDir/cluster-with-kaito.json"
kubectl get deployments,pods -A -o wide | grep -E 'kaito|gpu-provisioner'
kubectl get deployments -A -o json > "$KaitoDir/controllers.json"
kubectl wait --for=condition=Established crd/workspaces.kaito.sh --timeout=300s
kubectl get crd workspaces.kaito.sh -o yaml > "$KaitoDir/workspace-crd.yaml"
kubectl explain workspace.resource --api-version=kaito.sh/v1beta1
kubectl get workspaces.kaito.sh -A
```

Discover actual managed controller names/namespaces in this output and run `kubectl rollout status deployment/<name> -n <namespace> --timeout=300s` for each before proceeding. Record their images and ready/desired replica counts. Compare the deployed version with the reviewed preset and CRD; managed rollout versions can change. Stop for an unverified mismatch rather than installing a newer upstream CRD. The managed enablement path owns operator identity/federation; on a 403, inspect that identity's scope and controller events through current Microsoft guidance, not the application's Workload ID.

Require no existing Workspace before continuing. Apply the namespace guardrails:

```bash
kubectl apply -f ./advanced/kaito/namespace.yaml
kubectl -n kaito-lab get resourcequota,networkpolicy
kubectl -n kaito-lab get serviceaccount default -o yaml
kubectl -n kube-system get pods -l k8s-app=kube-dns --show-labels
```

The default service account does not mount an API token. Default-deny covers ingress and egress; separate egress permits cluster DNS and public HTTPS through the UDR firewall, excluding private/link-local destinations. No model caller is allowed yet. NetworkPolicy is additive, so inspect all policies before claiming isolation. GPU/device-plugin components live outside this application namespace and are managed by their platform owner, not by these PSA labels.

Do not install a CPU LimitRange/quota that silently makes the generated model containers inadmissible: the reviewed preset primarily specifies GPU resources. Capture actual CPU/memory/ephemeral-storage behavior and discuss a supported, resource-specified custom template for production. A dedicated GPU node and this synthetic workload are a lab boundary, not complete multi-tenant resource governance.

</details>

## 3. Reconcile the model and distinguish the readiness layers

**Challenge:** deploy the local, reviewed Workspace; trace resource provisioning, GPU advertising, artifact download and model readiness separately. Identify exactly which new Azure pool belongs to the exercise.

**Constraints:** keep count one and the specified SKU. No extra Workspaces, model-resource bypass annotations or repeated submissions to solve allocation delays. A timeout is a diagnostic/cleanup gate, not automatic deletion of billable resources.

**Exit evidence:** Workspace conditions, one GPU node and pool ID, allocatable GPU, model pod placement and artifact identities, internal Service and model-container readiness.

<details>
<summary>Solution</summary>

```bash
cat ./advanced/kaito/workspace.yaml
kubectl apply --dry-run=server -f ./advanced/kaito/workspace.yaml
ProvisionStart=$(date -u +%FT%TZ)
printf '%s\n' "$ProvisionStart" > "$KaitoDir/provision-start.txt"
kubectl apply -f ./advanced/kaito/workspace.yaml
# A bounded readiness wait replaces an unattended watch.
if ! kubectl -n kaito-lab wait workspace/kaito-phi4-mini --for=condition=InferenceReady --timeout=1800s; then
  kubectl -n kaito-lab get workspace,pods -o wide
  kubectl -n kaito-lab get events --sort-by=.lastTimestamp
  printf 'Readiness wait failed or expired: inspect the error, approve a new bounded window or complete task 7 cleanup.\n' >&2
  exit 1
fi
```

In a second management terminal, load `use-lab.sh` and inspect Azure pool count and events while the first wait runs:

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{name:name,count:count,size:vmSize,state:provisioningState,subnet:vnetSubnetId}' -o table
kubectl get nodes -l kaito-lab=phi4-mini -o wide
kubectl -n kaito-lab get pods -o wide
kubectl -n kaito-lab get events --sort-by=.lastTimestamp
```

Repeat inspection during provisioning, not unattended overnight. Interrupt the wait with Ctrl+C if necessary, not the operator. Microsoft's guide allows roughly ten minutes for machine readiness and twenty for Workspace readiness, varying with model and allocation. The wait has a 30-minute overall observation deadline; if it expires, collect diagnostics and either approve a new bounded window or go to task 7. Exiting the shell does not remove billable allocations.

Once conditions advance, in the original terminal:

```bash
kubectl -n kaito-lab wait workspace/kaito-phi4-mini --for=condition=ResourceReady --timeout=60s
kubectl -n kaito-lab wait workspace/kaito-phi4-mini --for=condition=InferenceReady --timeout=60s
kubectl -n kaito-lab rollout status deployment/kaito-phi4-mini --timeout=60s
kubectl -n kaito-lab get workspace kaito-phi4-mini -o yaml > "$KaitoDir/workspace-ready.yaml"
kubectl -n kaito-lab get pods -l kaito.sh/workspace=kaito-phi4-mini -o json > "$KaitoDir/model-pods.json"
kubectl -n kaito-lab get service kaito-phi4-mini -o yaml > "$KaitoDir/service.yaml"

GpuNodes=$(kubectl get nodes -l kaito-lab=phi4-mini -o json)
GpuNode=$(jq -ec '.items | if length == 1 then .[0] else error("Expected exactly one labeled GPU node; inspect allocation and cleanup") end' <<< "$GpuNodes")
GpuPool=$(jq -er '.metadata.labels.agentpool | select(type == "string" and length > 0)' <<< "$GpuNode")
jq -e '.status.allocatable["nvidia.com/gpu"] | tonumber == 1' <<< "$GpuNode" > /dev/null ||
  { printf 'One schedulable NVIDIA GPU is required.\n' >&2; exit 1; }
BeforePools=$(cat "$KaitoDir/pools-before.json")
if jq -e --arg pool "$GpuPool" 'any(.[]; .name == $pool)' <<< "$BeforePools" > /dev/null; then
  printf 'KAITO selected a pre-existing pool; stop and consult its owner.\n' >&2
  exit 1
fi
az aks nodepool show -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n "$GpuPool" -o json > "$KaitoDir/gpu-pool.json"
jq -er '.spec.providerID' <<< "$GpuNode" > "$KaitoDir/gpu-provider-id.txt"
kubectl describe node "$(jq -er '.metadata.name' <<< "$GpuNode")"
kubectl -n kaito-lab get deployment kaito-phi4-mini -o yaml
kubectl -n kaito-lab get pods --show-labels
```

Verify the recorded pool has count one, the approved SKU, Linux on a currently supported node image, the original nodes subnet/UDR path, and appropriate GPU taints. GPU availability requires both the driver and a functioning device plugin: a Ready node without `nvidia.com/gpu` is not inference capacity. Do not layer another plugin over the managed provisioner's stack. Match `status.workerNodes`, model pod `spec.nodeName` and the recorded GPU node.

The default Service must remain **ClusterIP**; do not add `kaito.sh/enablelb`, a LoadBalancer, NodePort or HTTPRoute. Inspect every generated Service port; only its HTTP port is needed, not Ray/dashboard ports. In the reviewed release, HTTP Service port 80 targets container port 5000. Its endpoints may be published before readiness, so endpoint presence does not replace pod readiness and a successful API call.

Retain both container `imageID` digests and the init-container command identifying the model OCI artifact. An init-container image digest identifies the downloader, **not the weights it downloads**. Resolve/record the model artifact's digest from downloader output/registry inspection and the release's model metadata for a reproducible promotion; do not claim the managed preset name pins every artifact. Compare elapsed provisioning time with model-startup time; none of these observations is inference latency.

</details>

## 4. Invoke privately and measure bounded inference, not just HTTP health

**Challenge:** discover the served model, obtain a nonempty answer to a synthetic support prompt, and record full-request latency/token usage without exposing an unauthenticated endpoint.

**Exit evidence:** served model ID, successful chat response, five timestamped samples with latency/tokens, qualitative answer review and actual cost observation. No fabricated throughput, quality score or production percentile from five samples.

<details>
<summary>Solution</summary>

On a second private-management terminal, check the same context and keep this foreground tunnel open:

```bash
kubectl config current-context
kubectl -n kaito-lab port-forward service/kaito-phi4-mini 18080:80 --address 127.0.0.1
```

In the original terminal:

```bash
InferenceUri='http://127.0.0.1:18080'
curl --fail-with-body --max-time 30 "$InferenceUri/v1/models" --output "$KaitoDir/models.json"
ModelId=$(jq -er '[.data[] | select(.id == "phi-4-mini-instruct") | .id] |
  if length == 1 then .[0] else error("Expected preset is not uniquely served; inspect runtime/version") end' "$KaitoDir/models.json")
Prompt='A synthetic order was accepted but is still processing. Explain briefly why acceptance is not fulfillment. Do not invent its status.'
Request=$(jq -n --arg model "$ModelId" --arg prompt "$Prompt" '{
  model: $model,
  messages: [
    {role: "system", content: "You explain synthetic order processing. You have no access to any order database."},
    {role: "user", content: $prompt}
  ],
  max_tokens: 64, temperature: 0, stream: false
}')
printf '[]\n' > "$KaitoDir/inference-samples.json"
for Attempt in {1..5}; do
  Started=$(date -u +%FT%TZ)
  if ElapsedSeconds=$(curl --fail-with-body --max-time 60 "$InferenceUri/v1/chat/completions" \
      --header 'Content-Type: application/json' --data "$Request" \
      --output "$KaitoDir/reply-$Attempt.json" --write-out '%{time_total}'); then
    :
  else
    CurlStatus=$?
    jq -n --argjson attempt "$Attempt" --arg started "$Started" --argjson status "$CurlStatus" \
      '{Attempt: $attempt, Started: $started, CurlExitStatus: $status}' > "$KaitoDir/failed-attempt.json"
    printf 'Inference HTTP/transport failure; retained response and failed-attempt evidence.\n' >&2
    exit "$CurlStatus"
  fi
  if ! jq -e '
    (.choices[0].message.content | type == "string" and test("\\S")) and
    (.usage.prompt_tokens | type == "number" and . >= 0) and
    (.usage.completion_tokens | type == "number" and . >= 0 and . <= 64) and
    (.choices[0].finish_reason | type == "string")
  ' "$KaitoDir/reply-$Attempt.json" > /dev/null; then
    jq -n --argjson attempt "$Attempt" --arg started "$Started" \
      '{Attempt: $attempt, Started: $started, Error: "Invalid reply schema, empty answer or token envelope breach"}' \
      > "$KaitoDir/failed-attempt.json"
    printf 'Invalid model reply; inspect retained response and failed-attempt evidence.\n' >&2
    exit 1
  fi
  Sample=$(jq --argjson attempt "$Attempt" --arg started "$Started" --argjson elapsed "$ElapsedSeconds" '{
    Attempt: $attempt, Started: $started, ElapsedMs: ($elapsed * 1000 | round),
    PromptTokens: .usage.prompt_tokens, CompletionTokens: .usage.completion_tokens,
    FinishReason: .choices[0].finish_reason
  }' "$KaitoDir/reply-$Attempt.json")
  jq --argjson sample "$Sample" '. + [$sample]' "$KaitoDir/inference-samples.json" > "$KaitoDir/inference-samples.tmp"
  mv "$KaitoDir/inference-samples.tmp" "$KaitoDir/inference-samples.json"
done
jq -r '(["Attempt","Started","ElapsedMs","PromptTokens","CompletionTokens","FinishReason"] | @tsv),
  (.[] | [.Attempt,.Started,.ElapsedMs,.PromptTokens,.CompletionTokens,.FinishReason] | @tsv)' "$KaitoDir/inference-samples.json"
kubectl -n kaito-lab top pods --containers
```

An HTTP or schema error stops the run; do not silently retry and erase failures. Retain completed per-request replies and record the failed attempt/time separately. `finish_reason: length` means the 64-token limit truncated the answer, not that the model crashed. Review whether the answer distinguishes acceptance from fulfillment and admits it cannot query orders. A fluent hallucination fails the quality review even when the endpoint is healthy; `temperature: 0` is not a correctness guarantee.

The first request may include warmup; compare it separately with later samples. These timings include complete non-streaming generation, not time-to-first-token. `kubectl top` reports CPU/memory, **not GPU utilization**. A production benchmark needs representative prompts/context lengths, concurrency, GPU memory/utilization, queue delay, time-to-first-token, output-token rate and an evaluation set. Installing a separate DCGM stack is outside this bounded exercise.

For cost discussion, combine the actual allocated-node duration with the approved regional rate and disk/network/logging costs. Cost Management data can lag. A low request count on a continuously allocated GPU can cost more than hosted per-token inference; no break-even claim follows from these five requests.

</details>

## 5. Prove caller isolation with a controlled denial and repair

**Challenge:** show that a successful administrator port-forward does not grant ordinary pods access. Demonstrate a DNS-resolved, timed-out model connection, then permit only the named synthetic client and prove recovery.

**Constraints:** keep the default-deny policies. Never permit all namespaces, open the model's auxiliary ports, or use the orders application's Azure identity for this probe. The probe has a 30-minute deadline and runs on `apps`, not a GPU node.

**Exit evidence:** resolved Service IP, denial, matching policy/labels, successful allowed request, and denial again after removing the temporary allowance.

<details>
<summary>Solution</summary>

Reuse the already built Python application image by its immutable reference; its app command is overridden and it gets no application secrets or Workload ID:

```bash
ImageReference=$(kubectl -n orders get deployment order-api -o 'jsonpath={.spec.template.spec.containers[0].image}')
[[ "$ImageReference" =~ @sha256:[a-f0-9]{64}$ ]] ||
  { printf 'Complete lab 4 immutable-image delivery before using the probe.\n' >&2; exit 1; }
ClientTemplate=$(cat ./advanced/kaito/client.yaml)
printf '%s\n' "${ClientTemplate//__IMAGE_REFERENCE__/$ImageReference}" > "$KaitoDir/client.yaml"
if grep -nE '__[A-Z0-9_]+__' "$KaitoDir/client.yaml"; then
  printf 'Unresolved client manifest placeholders.\n' >&2
  exit 1
fi
kubectl apply -f "$KaitoDir/client.yaml"
kubectl -n kaito-lab wait pod/inference-probe --for=condition=Ready --timeout=180s
kubectl -n kaito-lab get networkpolicy
Probe=$(cat <<'PY'
import json, socket, urllib.request, urllib.error
host = "kaito-phi4-mini.kaito-lab.svc.cluster.local"
ip = socket.gethostbyname(host)
try:
    with urllib.request.urlopen("http://" + host + "/v1/models", timeout=5) as response:
        print(json.dumps({"result": "allowed", "ip": ip, "status": response.status}))
except urllib.error.URLError as error:
    if not isinstance(error.reason, (TimeoutError, socket.timeout)):
        raise
    print(json.dumps({"result": "timeout", "ip": ip}))
except TimeoutError:
    print(json.dumps({"result": "timeout", "ip": ip}))
PY
)
kubectl -n kaito-lab exec inference-probe -- python -c "$Probe" > "$KaitoDir/caller-denied.json"
jq -e '.result == "timeout"' "$KaitoDir/caller-denied.json" > /dev/null ||
  { printf 'Expected an isolated caller; inspect all additive policies.\n' >&2; exit 1; }
```

DNS failures and connection-refused errors are not accepted as the intended policy timeout. Check the model is still Ready and `/v1/models` still succeeds through the existing tunnel, then apply the narrow allowance:

```bash
curl --fail-with-body --max-time 10 "$InferenceUri/v1/models"
kubectl apply -f ./advanced/kaito/allow-client.yaml
sleep 10
kubectl -n kaito-lab exec inference-probe -- python -c "$Probe" > "$KaitoDir/caller-allowed.json"
jq -e '.result == "allowed" and .status == 200' "$KaitoDir/caller-allowed.json" > /dev/null ||
  { printf 'Expected allowed caller; inspect Service targetPort and both policy directions.\n' >&2; exit 1; }
```

The policies allow ingress only to pods labeled `kaito.sh/workspace=kaito-phi4-mini`, from `app=inference-probe` **in this namespace**, and matching client egress to TCP 5000, the pod target port. They do not allow Ray/dashboard traffic. If service translation behaves differently on the live dataplane, inspect Cilium flow evidence before changing policy; do not replace a precise allowance with all ports/destinations.

Remove the temporary allowance and verify the new connection is denied again:

```bash
kubectl delete -f ./advanced/kaito/allow-client.yaml
sleep 10
kubectl -n kaito-lab exec inference-probe -- python -c "$Probe" > "$KaitoDir/caller-denied-again.json"
jq -e '.result == "timeout"' "$KaitoDir/caller-denied-again.json" > /dev/null ||
  { printf 'Isolation was not restored; inspect policy propagation and connection state.\n' >&2; exit 1; }
kubectl -n kaito-lab delete pod inference-probe
```

This deny/allow/deny sequence plus unchanged model health provides stronger attribution than one timeout. Port-forward follows the authorized Kubernetes API/kubelet path and is not an ordinary pod-to-Service NetworkPolicy test. Restrict `pods/portforward`, `pods/exec` and namespace write access separately. Someone allowed to create labeled pods in this namespace can impersonate the probe label; NetworkPolicy is not end-user authentication. Production needs an authenticated gateway, authorization, rate/token limits, TLS and safety controls before serving customers.

</details>

## 6. Diagnose the failing layer without buying a larger GPU

**Challenge:** classify actual failures using control-plane, scheduler, image, runtime and caller evidence. If your run is healthy, explain the incident branches below without claiming to have executed them.

**Exit evidence:** a short incident record for task 5 plus any actual provisioning/runtime failure, its supporting event/error, repair and post-repair check. No quota-exhaustion or GPU-OOM fault injection is required.

<details>
<summary>Solution</summary>

```bash
kubectl -n kaito-lab describe workspace kaito-phi4-mini
kubectl -n kaito-lab get events --sort-by=.lastTimestamp
kubectl -n kaito-lab describe pod -l kaito.sh/workspace=kaito-phi4-mini
kubectl -n kaito-lab logs deployment/kaito-phi4-mini -c model-weights-downloader --tail=80
kubectl -n kaito-lab logs deployment/kaito-phi4-mini -c kaito-phi4-mini --tail=80
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -o table
```

Run log commands separately and only for containers discovered in the pod. A container that never started has no runtime logs; that absence is useful, not an excuse to ignore scheduler or init-container evidence. Discover controller names from task 2 for platform logs. Retain only synthetic data and redact identity/personal information before sharing.

| Observation | Evidence and repair |
|---|---|
| Workspace API absent or controller unavailable | CRD served versions, managed add-on state, controller rollout/events. Repair the supported add-on; never apply an arbitrary upstream CRD. |
| `ResourceReady=False`, no usable pool | Azure provisioning error, GPU-family/total quota, SKU allocation and operator identity scope. A 403, quota error and capacity error need different repairs. Do not repeatedly recreate the Workspace or increase count. |
| Node Ready, GPU absent or pod Pending | `allocatable`, device-plugin health, pod GPU request, labels/taints, namespace quota and admission events. Node CPU headroom says nothing about available GPU memory. |
| Image pull or model downloader fails | Container `imageID`, init-container logs, artifact address, DNS and firewall denies. Runtime image access does not prove model-artifact access. Fix the exact allowed endpoint or supported artifact; no public ACR/admin credential workaround. |
| GPU OOM, repeated restarts or slow startup | Model/runtime logs, actual context/batch configuration, GPU capacity, node memory/disk pressure. Restore supported preset settings; smaller context or a different model requires a reviewed change. Do not add resource-check bypass annotations. |
| `/v1/models` works but inference fails | Served model name, `/v1/chat/completions` payload, context/token limits and runtime error. An unsupported model ID/API path is not a networking outage. |
| Port-forward works but probe times out | Both ingress/egress policies, pod labels, Service target port, DNS and the task-5 positive control. Restore the scoped caller allowance, not public exposure. |

For model changes, treat the Workspace/runtime configuration as the desired source. Do not edit the generated Deployment or use `kubectl rollout undo` against the operator. Preserve a known-good manifest and artifact identities, assess model/license/config compatibility, apply a reviewed Workspace change and repeat inference/quality checks. With one GPU and the reviewed zero-surge model Deployment, updates can interrupt service; this lab does not claim high availability or an executed model upgrade.

</details>

## 7. Remove the Workspace and its GPU bill, then prove the baseline survived

**Challenge:** end the experiment in the same session, including failed/partial allocations. Delete only resources whose ownership is established against the pre-change inventory. Keep orders, its autoscalers, regular pools, Flux, network and monitoring intact.

**Safety boundary:** delete the Workspace **before** its pool so reconciliation cannot recreate capacity. Workspace deletion alone does not delete GPU node pools. Do not use AKS stop/start as a shortcut with active KAITO Workspaces; Microsoft documents reconciliation/orphan risks. If finalizers block, inspect the managed controller and complete its supported cleanup rather than stripping finalizers.

**Exit evidence:** no lab Workspace/pods/Service, all identified new GPU pools removed from AKS and Azure inventory, recorded add-on disposition, unchanged baseline pool names/healthy orders GitOps, and a later cost check for residual charges.

<details>
<summary>Solution</summary>

Stop the port-forward terminal with Ctrl+C. In the original terminal (or after `set -euo pipefail`, sourcing `./scripts/use-lab.sh` and setting `KaitoDir="$Root/.artifacts/kaito"` on re-entry), first retain the live allocation inventory, including failed pools that never produced a node:

```bash
BeforePools=$(cat "$KaitoDir/pools-before.json")
CurrentPools=$(az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -o json)
NewPools=$(jq --argjson before "$BeforePools" '[.[] | select(.name as $name | all($before[]; .name != $name))]' <<< "$CurrentPools")
printf '%s\n' "$NewPools" > "$KaitoDir/new-pools-before-cleanup.json"
jq '.[] | {name,id,vmSize,count,provisioningState,nodeLabels}' <<< "$NewPools"
HasNamespace=$(kubectl get namespace kaito-lab --ignore-not-found -o name)
HasWorkspaceApi=$(kubectl get crd workspaces.kaito.sh --ignore-not-found -o name)
if [[ -n "$HasNamespace" ]]; then
  kubectl -n kaito-lab get pods,service -o wide
  if [[ -n "$HasWorkspaceApi" ]]; then kubectl -n kaito-lab get workspace -o wide; fi
fi
kubectl get nodes -l kaito-lab=phi4-mini -o wide
```

Correlate each candidate with this Workspace's labels, recorded pool/provider IDs, creation time and managed provisioner/Activity Log evidence. **A new pool is a candidate, not deletion authorization.** A partial failure might leave a pool with no node label; use its ARM provisioning record and controller evidence. Resolve ambiguous/shared ownership before deleting anything, and do not let an unresolved allocation disappear from the cleanup record.

```bash
if [[ -n "$HasNamespace" ]]; then
  if [[ -n "$HasWorkspaceApi" ]]; then
    kubectl -n kaito-lab delete workspace kaito-phi4-mini --ignore-not-found --wait=true --timeout=300s
  fi
  HasDeployment=$(kubectl -n kaito-lab get deployment kaito-phi4-mini --ignore-not-found -o name)
  if [[ -n "$HasDeployment" ]]; then kubectl -n kaito-lab wait --for=delete deployment/kaito-phi4-mini --timeout=180s; fi
  kubectl -n kaito-lab get pods,service
fi
```

Require generated model workloads to be gone before deleting a GPU pool. A failed deployment might never have created the namespace/Deployment; record that absence rather than recreating it for cleanup. If the Workspace API was unexpectedly removed while its workloads/pools remain, stop and restore supported controller cleanup rather than treating the missing API as success. If a probe/allowance remains after interruption, remove only those known lab resources:

```bash
if [[ -n "$HasNamespace" ]]; then
  kubectl delete -f ./advanced/kaito/allow-client.yaml --ignore-not-found
  kubectl -n kaito-lab delete pod inference-probe --ignore-not-found
fi
```

For **each** positively identified lab GPU pool, execute this block individually. It deliberately refuses a baseline pool or an unreviewed SKU:

```bash
read -r -p 'Exact verified lab GPU pool name to delete: ' DeletePool
if [[ -z "$DeletePool" ]] || jq -e --arg pool "$DeletePool" 'any(.[]; .name == $pool)' <<< "$BeforePools" > /dev/null; then
  printf 'Refusing an empty or baseline pool name.\n' >&2
  exit 1
fi
Candidate=$(jq --arg pool "$DeletePool" '[.[] | select(.name == $pool)]' <<< "$NewPools")
jq -e 'length == 1 and .[0].vmSize == "Standard_NC24ads_A100_v4"' <<< "$Candidate" > /dev/null ||
  { printf 'Pool does not match the reviewed new GPU inventory; investigate before deletion.\n' >&2; exit 1; }
az aks nodepool show -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n "$DeletePool" -o json
read -r -p "Type $DeletePool to confirm deletion after verifying ownership: " ConfirmPool
[[ "$ConfirmPool" == "$DeletePool" ]] || { printf 'Deletion not confirmed.\n' >&2; exit 1; }
az aks nodepool delete -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n "$DeletePool"
```

Wait for completion; do not use `--no-wait` and immediately assume charges stopped. If no new pool ever existed, retain that inventory as the reason no pool deletion ran. Inspect the node resource group for remaining VMSS/VM/disk resources attributable to this attempt, including any failed allocations not present in the AKS pool list:

```bash
ClusterNow=$(az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o json)
az resource list -g "$(jq -er '.nodeResourceGroup' <<< "$ClusterNow")" -o table
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -o json > "$KaitoDir/pools-after.json"
AfterPools=$(cat "$KaitoDir/pools-after.json")
jq --argjson before "$BeforePools" '{
  Removed: (($before | map(.name)) - (map(.name))),
  Added: ((map(.name)) - ($before | map(.name)))
}' <<< "$AfterPools" > "$KaitoDir/pool-differences.json"
cat "$KaitoDir/pool-differences.json"
jq -e '.Removed == [] and .Added == []' "$KaitoDir/pool-differences.json" > /dev/null ||
  { printf 'Pool inventory differs from baseline; resolve ownership and cleanup.\n' >&2; exit 1; }
kubectl get nodes -l kaito-lab=phi4-mini
```

Expected: no name differences and no lab GPU nodes. This comparison does not prove all external disks/VMs were deleted; inspect the recorded Azure resources too. Do not manually delete arbitrary AKS-managed VMSS instances to hide an orphan; resolve failed AKS provisioning through supported recovery/support with the resource IDs.

Only after Workspace/pool cleanup, delete the lab namespace. If this exercise newly enabled the add-on and no other Workspace/owner now uses it, restore the original disabled state through the managed command; otherwise explicitly retain it with its owner's agreement:

```bash
if [[ -n "$HasWorkspaceApi" ]]; then kubectl get workspaces.kaito.sh -A; fi
if [[ -n "$HasNamespace" ]]; then kubectl delete namespace kaito-lab --wait=true --timeout=180s; fi
# Only if the pre-change record shows the add-on was absent and no other owner uses it:
read -r -p 'Type DISABLE only if the baseline was disabled and no other owner uses KAITO (otherwise RETAIN): ' AddonDisposition
case "$AddonDisposition" in
  DISABLE) az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --disable-ai-toolchain-operator ;;
  RETAIN) printf 'Managed add-on retained with platform-owner agreement.\n' ;;
  *) printf 'Add-on disposition not confirmed.\n' >&2; exit 1 ;;
esac
printf '%s\n' "$AddonDisposition" > "$KaitoDir/addon-disposition.txt"
```

Review `cluster-before.json` against current add-on state before that final command; it is not an unconditional cleanup step. Retain managed CRDs/identities according to the supported disable lifecycle, not by blanket deletion of `kaito`/`karpenter` resources or role assignments. Inventory any remaining add-on identity/federation and document its owner/disposition.

```bash
flux get kustomizations -A
kubectl -n orders get deployments,hpa,scaledobjects
kubectl -n orders describe scaledobject order-worker
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -o table
read -r -p 'Currently active trusted orders HTTPS URL: Lab 3 private or Lab 10 edge: ' OrdersUri
OrdersUri="${OrdersUri%/}"
[[ "$OrdersUri" == https://* ]] || { printf 'Supply the trusted HTTPS URL.\n' >&2; exit 1; }
curl --fail-with-body --max-time 30 "$OrdersUri/readyz"
```

Require healthy orders reconciliation/API and a healthy worker scaler; zero workers with an empty queue is normal. Preserve the primary cluster's progressed settings, not a redeployment of `infra/main.bicep`. Retain redacted evidence locally, check Cost Management after its reporting delay, and resume the next cumulative lab. GPU cleanup is immediate; regular lab infrastructure remains until the README's final teardown.

</details>

## Customer discovery and model answers

<details>
<summary>Model answer: When is KAITO preferable to a hosted model API?</summary>

When control over deployment, model/runtime customization, data handling or sustained GPU utilization justifies operating the infrastructure. Compare workload shape, latency, model quality/licensing, regional GPU supply, patching and total operating cost. A managed API removes much of that capacity work; neither option is universally cheaper or automatically compliant.

</details>

<details>
<summary>Model answer: Does the AKS or KAITO managed service own the whole AI solution?</summary>

No. AKS manages the cluster service and supported add-on lifecycle; KAITO reconciles the specified model workspace. The customer still owns application authentication, data classification, model selection/evaluation, abuse controls, availability design, observability, cost and recovery. A healthy Workspace is not a business-quality guarantee.

</details>

<details>
<summary>Model answer: Is this endpoint private and authenticated?</summary>

It is ClusterIP-only, pod-isolated by default and accessed through an authorized loopback Kubernetes tunnel. The model HTTP API itself has no end-user authentication or TLS in this exercise. Pod labels, private addresses and Workload ID do not authenticate customers; a production serving layer must do that explicitly.

</details>

<details>
<summary>Model answer: Will the Lab 6 HPA/KEDA configuration scale the model?</summary>

No. Those controllers target orders Deployments, not this Workspace. CPU utilization is often a poor GPU-serving signal; requests, token/context length, queue delay and GPU memory/utilization matter. Pod replicas, GPU provisioning and model-loading time are different loops. Test a supported single-owner scaling design; do not attach a second replica controller to KAITO-generated resources.

</details>

<details>
<summary>Model answer: Can we scale to zero or use Spot to make the lab free when idle?</summary>

Not with this configuration. One allocated regular GPU keeps billing between requests, and deleting its Workspace does not delete its pool. Cold model downloads/startup affect latency, and Spot eviction can interrupt generation. Capacity replacement, retry semantics and any scale-to-zero feature need separate support and SLO validation.

</details>

<details>
<summary>Model answer: Is a successful answer enough for production acceptance?</summary>

No. Validate domain quality, hallucinations/refusal behavior, prompt injection, data leakage, license constraints and representative latency/throughput. This model has no access to orders; claiming an order's actual status is a failure. RAG and fine-tuning solve different problems and introduce additional data/identity/lifecycle responsibilities; neither was executed here.

</details>

## References and support

The local manifests adapt the documented API shape for an isolated, synthetic lab. No Azure deployment, GPU allocation, inference benchmark, role change or live cleanup was executed while authoring. Resolve current managed-version/support and regional capacity gates in your subscription before claiming the exit evidence.

- [Managed AKS AI toolchain operator: prerequisites, version, deployment and cleanup](https://learn.microsoft.com/en-us/azure/aks/ai-toolchain-operator)
- [KAITO 0.6.0 Phi-4-mini example](https://github.com/kaito-project/kaito/blob/v0.6.0/examples/inference/kaito_workspace_phi_4_mini.yaml)
- [KAITO 0.6.0 Workspace API](https://github.com/kaito-project/kaito/blob/v0.6.0/api/v1beta1/workspace_types.go)
- [KAITO 0.6.0 inference and update behavior](https://github.com/kaito-project/kaito/blob/v0.6.0/website/docs/inference.md)
- [KAITO 0.6.0 model versions/artifacts](https://github.com/kaito-project/kaito/blob/v0.6.0/presets/workspace/models/supported_models.yaml)
- [Phi-4-mini-instruct model card and license](https://huggingface.co/microsoft/Phi-4-mini-instruct)
- [AKS NVIDIA GPU prerequisites and device-plugin responsibilities](https://learn.microsoft.com/en-us/azure/aks/use-nvidia-gpu)
- [Fully managed GPU node pools: separate preview/support boundary](https://learn.microsoft.com/en-us/azure/aks/aks-managed-gpu-nodes)
- [Kubernetes NetworkPolicy semantics](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
