# Optional workshop — Workload reliability without disturbing orders

**Entry:** complete [lab 5](../labs/05-acr-and-workloads.md) or [lab 13](../labs/13-gitops.md).
**Time:** 60–90 minutes. **Mode:** approved, bounded live practice; rendering/tests are offline.
**Cost:** existing cluster CPU/memory, private ACR pulls and log ingestion; no new Azure service,
node, public endpoint or disk. Existing infrastructure remains billable.
**Exit:** distinguish the three probes, container OOM and eviction; retain termination/rollout
observations under bounded traffic; delete only the workshop namespace.

Read the shared [operating contract](../docs/operations.md) and [state/ownership model](../docs/state.md).
This is not a numbered lab and does not change the cumulative environment's progression.
The incident owner owns **only `workload-reliability`**, never `orders`, `orders-test`, a
Flux inventory, HPA, KEDA object, PDB, grant or node. After lab 13, these two diagnostic
replicas remain outside application controllers; do not enroll them in Flux or scaling.
Do not render the cumulative base or rerun GitOps initialization.

## 1. Approve the context and resource envelope before applying

Use Linux, Bash 5+, Python 3.12+, `jq`, `kubectl` (with Kustomize) and GNU `timeout`,
from the repository root. Reuse the current verified native Bastion API session; never
overwrite its kubeconfig with `get-credentials`. The kubelet still needs its existing
private ACR connectivity and pull permission. Bastion is not an ACR/data-plane route.

Use the already approved **`order-app@sha256:…` digest from the existing foundation ACR**.
The fixture overrides its command with checked-in Python standard-library code: it does
not start the orders application, access Service Bus, use workload identity, or download
packages/images from the Internet. Do not rebuild/publish an image for this workshop.

Prepare and inspect locally first (the renderer never contacts a cluster):

```bash
set -euo pipefail
source ./scripts/use-lab.sh
Registry=$(lab_value RegistryServer)
read -r -p 'Approved existing ACR order-app@sha256 digest reference: ' Image
Work="$PWD/.artifacts/workload-reliability"
python3 workshops/assets/reliability/render.py \
  --registry "$Registry" --image "$Image" --output "$Work"
kubectl kustomize "$Work" > "$Work/review.yaml"
diff -u "$Work/bundle.yaml" "$Work/review.yaml"
```

An existing output directory is rejected, not overwritten. After a failed render inspect
it; do not apply partial output. On re-entry use the previously reviewed render only if
its source/digest still match the approved exercise; otherwise choose a new local output
directory. Never apply the unresolved checked-in fixtures directly.

Review `review.yaml`, `memory-job.yaml` and `traffic-job.yaml` together. The base does
**not** create either Job; they run separately, once, later:

| Boundary | Approved maximum / behavior |
|---|---|
| Namespace | Exactly `workload-reliability`; must not already exist |
| Diagnostic | 2 replicas; rolling surge 1, unavailable 0; terminating pods can temporarily add to total |
| Per diagnostic | requests 100m CPU / 64 MiB; limits 200m / 128 MiB |
| Traffic | One sequential client, 120 seconds, two-second request timeout; Job deadline 150 seconds, no retry |
| Memory probe | One container, request=limit 100m / 64 MiB, at most 96 MiB attempted allocation; eight-second allocation bound, Job deadline 30 seconds, no retry |
| Namespace quota | 6 pods; requests 1 CPU / 512 MiB; limits 2 CPU / 768 MiB; ephemeral requests 128 MiB / limits 256 MiB |
| Storage / identity | 8 MiB disk-backed `emptyDir` per pod; no PVC/token/identity/grant |
| Network | ClusterIP only; diagnostic ingress from workshop traffic; egress only diagnostic and kube-system DNS |

The quota limits **workshop consumption**, not available node headroom. An authorized
operator must review apps-pool placement, allocatable resources, existing reservations,
current utilization and node conditions and approve this envelope. If there is pressure,
insufficient headroom, a policy/admission conflict, an unrecognized DNS path, or no approved
capacity evidence, stop: do not resize nodes, weaken policy or create memory pressure.
`kubectl top` may be unavailable after lab 5; missing metrics are not evidence of spare memory.

Read-only preflight and a deliberate approval gate:

```bash
Context=$(kubectl config current-context)
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}{"\n"}'
kubectl get nodes -l kubernetes.azure.com/agentpool=apps -o wide
kubectl describe nodes -l kubernetes.azure.com/agentpool=apps
Existing=$(kubectl get namespace workload-reliability --ignore-not-found -o name)
[[ -z "$Existing" ]] || { echo 'Namespace exists: stop; never adopt/delete another run.' >&2; exit 1; }
kubectl auth can-i create namespaces
kubectl auth can-i create deployments -n workload-reliability
# Have the authorized operator approve capacity, permissions and the reviewed three manifests.
printf 'Context: %s\nEnvelope: workload-reliability, 6 pods, 2 CPU / 768 MiB limits\n' "$Context"
read -r -p "Type '$Context workload-reliability approved' after approval: " Approval
[[ "$Approval" == "$Context workload-reliability approved" ]] || exit 1
[[ "$(kubectl config current-context)" == "$Context" ]] || exit 1
ws() {
  timeout 180s kubectl --context="$Context" -n workload-reliability "$@"
}
collect() {
  ws get pods -o wide
  ws get events --sort-by=.lastTimestamp
  ws describe deployment diagnostic
}
# Create fails if another run claimed this name after preflight; never adopt it.
ws create -f "$Work/namespace.yaml"
# Enforce the quota before creating any workload, even if the later apply is partial.
ws apply -f "$Work/budget.yaml"
ws apply -k "$Work"
```

Review permissions for ConfigMaps, Service, NetworkPolicy, ResourceQuota, Jobs, pods/log
and pods/exec as well; an existing authorized operator may run these steps, but this
workshop creates **no RBAC grants**. `auth can-i` must return yes/success, not merely print
something. Never treat an API error while checking namespace absence as an absent namespace.

If apply partially fails, retain the error and inspect this namespace. Do not continue to
faults or traffic; repair only a reviewed workshop manifest or use the exact cleanup below.
All later commands retain the explicitly approved context through `ws`.

## 2. Startup is not readiness, and readiness is not liveness

The Python server binds promptly but returns `/startupz` = 503 for its first 12 seconds.
The startup probe gives it up to about 40 seconds and suppresses readiness/liveness until
it passes. Startup failures during this window are expected, not repeated crashes.
Once started, `/livez` checks this process, while `/readyz` decides whether it should
receive new work. These deliberately local probes prove no Azure dependency or business SLO.

Observe a bounded series of snapshots while the initial pods start:

```bash
for step in {1..8}; do
  ws get pods -l app=diagnostic -o json | jq '.items[] | {
    pod:.metadata.name, phase:.status.phase,
    containers:[.status.containerStatuses[]? | {started,ready,restartCount}]}'
  sleep 2
done
if ! ws rollout status deployment/diagnostic --timeout=120s; then
  collect
  echo 'Startup did not converge: stop and inspect; do not relax probes blindly.' >&2
  exit 1
fi
Pod=$(ws get pods -l app=diagnostic -o json |
  jq -er '[.items[] | select(.status.containerStatuses[0].ready == true)][0].metadata.name')
Before=$(ws get pod "$Pod" -o jsonpath='{.status.containerStatuses[0].restartCount}')
ws logs "$Pod" --tail=20
```

Record initial `started:false`, then `started:true/ready:true`, and unchanged restart
counts if you captured that interval. Image-pull/scheduling delays may hide the startup
window; report that rather than inventing an observation. Read probe configuration/log
timestamps to explain the gate. Do not delete pods just to produce a prettier screenshot.

### Readiness-only fault: remove one endpoint, not one process

```bash
ws exec "$Pod" -- python -B /work/diagnostic.py fault ready 20
if ! ws wait --for=condition=Ready=false "pod/$Pod" --timeout=15s; then
  collect
  echo 'Readiness transition not observed; fault expires automatically. Stop here.' >&2
  exit 1
fi
ws get endpointslices -l kubernetes.io/service-name=diagnostic -o json |
  jq '[.items[].endpoints[] | {addresses,conditions,targetRef}]'
if ! ws wait --for=condition=Ready=true "pod/$Pod" --timeout=60s; then collect; exit 1; fi
After=$(ws get pod "$Pod" -o jsonpath='{.status.containerStatuses[0].restartCount}')
[[ "$After" == "$Before" ]] || { echo 'Unexpected restart: inspect before continuing.' >&2; exit 1; }
```

Expected evidence: the selected endpoint is unready (or withdrawn), the other stays ready,
then readiness returns **without a restart**. A readiness failure is not an instruction to
kill the process. Readiness faults expire after 20 seconds, even if the terminal disconnects.

### Liveness-only fault: restart once, then recover

```bash
ws exec "$Pod" -- python -B /work/diagnostic.py fault live 20
Restarted=false
for step in {1..30}; do
  After=$(ws get pod "$Pod" -o jsonpath='{.status.containerStatuses[0].restartCount}')
  if (( After > Before )); then Restarted=true; break; fi
  sleep 2
done
if [[ "$Restarted" != true ]]; then collect; echo 'No restart observed; stop.' >&2; exit 1; fi
if ! ws wait --for=condition=Ready=true "pod/$Pod" --timeout=60s; then collect; exit 1; fi
ws logs "$Pod" --previous --tail=20
ws describe pod "$Pod"
```

Expected evidence: failed liveness events followed by kubelet termination/restart, an
increased restart count, another startup gate, then Ready. The fault deadline is stored
in `emptyDir` across the restart and expires; it is not rearmed on every process start.
The preStop/drain plus startup interval normally outlast the remaining fault. If repeated
restarts occur, stop and collect evidence; never weaken the shared orders probes.
`--previous` can fail if logs are unavailable: that is missing evidence, not proof of success.

## 3. Container OOM is not node eviction

This is a **real container-cgroup memory-limit probe**, not a fake exit 137. It refuses to
allocate unless it sees a finite container limit of at most 64 MiB, touches at most 96 MiB,
and never loops indefinitely. Do not run `memory.py` on the host, increase its budget, run
multiple copies or induce eviction. Reconfirm the approved headroom before this step.

```bash
ws create -f "$Work/memory-job.yaml"
# Failure is expected; a timeout is NOT the same as an observed OOM.
if ! ws wait --for=condition=Failed job/memory-probe --timeout=75s; then
  ws get job memory-probe -o yaml
  ws get pods -l job-name=memory-probe -o yaml
  echo 'No bounded failure observed; inspect and delete this Job, do not scale it.' >&2
  exit 1
fi
ws logs job/memory-probe --tail=30
ws get pods -l job-name=memory-probe -o json > "$Work/memory-result.json"
jq '.items[] | {pod:.metadata.name, phase:.status.phase, reason:.status.reason,
  containers:[.status.containerStatuses[]? | .state.terminated]}' "$Work/memory-result.json"
if ! jq -e 'any(.items[]; any(.status.containerStatuses[]?;
  .state.terminated.reason == "OOMKilled" and .state.terminated.exitCode == 137))' \
  "$Work/memory-result.json" >/dev/null; then
  echo 'OOM not proven (deadline, refusal or other error); stop, do not enlarge limits.' >&2
  exit 1
fi
ws get events --sort-by=.lastTimestamp
ws delete job memory-probe --wait=true --timeout=60s
```

Expected OOM evidence is a **container termination** `reason: OOMKilled`, typically code
137, with this Job's 64 MiB limit. Exit 137 alone is ambiguous. A **pod eviction** instead
has pod-level `status.reason: Evicted` and an eviction message/event, often alongside
node resource pressure; it is not established by the container code alone.
If eviction/node pressure appears, stop the workshop immediately, collect read-only
evidence, notify the operator and remove only workshop resources. Never manufacture
eviction to complete a worksheet. The memory Job must be deleted before traffic/rollout.

## 4. Observe graceful termination and a rolling update under traffic

First require two Ready replicas again. This is a short observation window, **not a
guaranteed SLO or statistically valid availability test**. The traffic Job sends one
request at a time through Service DNS/ClusterIP, not a port-forward pinned to one pod.
It logs each status/latency/pod/revision and a final summary; any failed request makes
the Job fail (without retries). Scheduling/DNS/policy failures are different from a
rollout-induced error.

```bash
ws rollout status deployment/diagnostic --timeout=120s
ws create -f "$Work/traffic-job.yaml"
if ! ws wait --for=jsonpath='{.status.active}'=1 job/traffic --timeout=45s; then
  collect; ws describe job traffic; exit 1
fi
ws logs job/traffic --pod-running-timeout=30s --tail=3
```

Before updating, require actual `status:200, revision:v1` traffic, not merely an active
Job. Start this bounded log capture in a **second terminal** with the same approved context
and exact active Bastion `KUBECONFIG` (do not acquire replacement credentials). It attaches
to the existing v1 pods so deletion does not erase their last lifecycle messages:

```bash
set -euo pipefail
read -r -p 'Exact approved context from the first terminal: ' Context
[[ "$(kubectl config current-context)" == "$Context" ]] || exit 1
mkdir -p .artifacts/workload-reliability
if timeout 100s kubectl --context="$Context" -n workload-reliability logs \
  -l app=diagnostic --prefix --follow --max-log-requests=4 \
  > .artifacts/workload-reliability/termination.log 2>&1; then
  echo 'Log streams closed.'
else
  Status=$?
  [[ "$Status" == 124 ]] || { echo "Capture failed: $Status; inspect log." >&2; exit "$Status"; }
  echo 'Bounded capture ended; inspect for missing lifecycle evidence.'
fi
```

While that capture runs, return to the **first terminal**:

```bash
ws set env deployment/diagnostic REVISION=v2
if ! ws rollout status deployment/diagnostic --timeout=120s; then
  collect
  # Restore only the workshop revision; this is not a cumulative GitOps rollback.
  ws set env deployment/diagnostic REVISION=v1
  if ! ws rollout status deployment/diagnostic --timeout=120s; then collect; fi
  echo 'Rollout failed; stop traffic with the recovery command below.' >&2
  exit 1
fi
if ws wait --for=condition=Complete job/traffic --timeout=160s; then
  echo 'Bounded traffic completed without recorded request failures.'
else
  echo 'Traffic failed/timed out: this is NOT a passing availability observation.' >&2
  ws describe job traffic
fi
ws logs job/traffic --tail=300 > "$Work/traffic.log"
ws get deployment diagnostic -o yaml > "$Work/deployment-after.yaml"
ws get endpointslices -l kubernetes.io/service-name=diagnostic -o yaml > "$Work/endpoints-after.yaml"
```

Compare timestamps: v1 requests, new v2 startup, v2 Ready, old endpoints terminating,
`sigterm` then `stopped`, and v2 requests. The exec preStop sets a drain marker and waits
three seconds for endpoint propagation; its stdout is not normally in container logs.
SIGTERM stops new work and gives another three seconds before shutdown; accepted work
takes 0.2 seconds and is allowed to finish. The full termination grace is 15 seconds and
includes the hook; endpoint propagation is asynchronous. Capture gaps, network errors,
forced SIGKILL and v1/v2 coverage honestly. If rollout starts after traffic ends or no v2
samples exist, the overlap experiment is inconclusive, even if both commands succeeded.

Explain why `maxUnavailable:0` plus readiness reduces disruption but cannot promise zero
errors, why an aggressive liveness probe can worsen an overloaded dependency, and why
a PDB is not the controller's rolling-update budget. No PDB is created or changed here.

## Recovery, acceptance and exact cleanup

On any failed check, stop proceeding. Fault deadlines recover automatically; collect
pods/events/logs and try a bounded Ready wait. Stop the two bounded Jobs if present:

```bash
ws delete job traffic memory-probe --ignore-not-found --wait=true --timeout=60s
if ! ws rollout status deployment/diagnostic --timeout=120s; then collect; fi
```

If recovery fails, preserve evidence and remove the isolated namespace; do not modify
cluster policy, quotas outside this namespace, nodes or cumulative app replicas.

Acceptance requires the probe transitions/restart comparison, an actual OOM termination
(or an explicitly recorded non-demonstration), an OOM-versus-eviction explanation,
traffic summary including errors, rollout overlap, and graceful-termination evidence.
Missing evidence means **inconclusive**, not pass. No measured result is supplied here.

After exporting evidence locally, confirm ownership before deleting **exactly** this
namespace. This also handles a partially applied run. A missing namespace is already clean;
an API error or a mismatched owner is not authorization to delete anything:

```bash
Run=$(cat "$Work/run-id")
[[ "$Run" =~ ^[0-9a-f]{32}$ ]] || exit 1
Existing=$(ws get namespace workload-reliability --ignore-not-found -o name)
if [[ -n "$Existing" ]]; then
  Owner=$(ws get namespace workload-reliability \
    -o jsonpath='{.metadata.labels.workshops\.aksrepetition/owner}')
  ActualRun=$(ws get namespace workload-reliability \
    -o jsonpath='{.metadata.labels.workshops\.aksrepetition/run}')
  [[ "$Owner" == workload-reliability && "$ActualRun" == "$Run" ]] ||
    { echo 'Owner/run mismatch: stop; this is not your namespace.' >&2; exit 1; }
  read -r -p "Type '$Context workload-reliability delete' to delete only this namespace: " Approval
  [[ "$Approval" == "$Context workload-reliability delete" ]] || exit 1
  ws delete namespace workload-reliability --wait=true --timeout=120s
fi
Remaining=$(ws get namespace workload-reliability --ignore-not-found -o name)
[[ -z "$Remaining" ]] || { echo 'Namespace still terminating: inspect, never strip finalizers.' >&2; exit 1; }
```

Retain `.artifacts/` evidence privately; never commit kubeconfigs, secrets or measured
incident data. If your shell was lost, restore the existing approved context/`Work`/`ws`
definitions and review ownership before recovery; do not reapply the whole exercise.
The render's unique `run-id` prevents cleanup from claiming another run that raced with
your namespace creation. Preserve that local file until cleanup is verified.

## References and onward route

- [Kubernetes probes](https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/)
- [Pod termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination)
- [Node-pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)
- [README lab index](../README.md#lab-index)

**Next:** after lab 5, continue [lab 6 — troubleshooting and lifecycle](../labs/06-troubleshooting-and-lifecycle.md);
after lab 13, continue [lab 14 — observability](../labs/14-observability.md).
This optional detour does not renumber or replace either path.
