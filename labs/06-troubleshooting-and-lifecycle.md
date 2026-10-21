# Lab 6 - Diagnose, recover and hand over an AKS baseline

**Level:** beginner. **Scenario:** a deployment stalls while existing replicas still serve traffic. Diagnose the real failure, repair it, and leave the next operator a supported, understandable environment.

**Outcome:** a verified scheduling recovery, a basic lifecycle/cost runbook and a safe handoff to the intermediate track without redeployment.

## Lab contract

| Field | This lab |
|---|---|
| Entry | Lab 5 healthy initial workload; before identity/network/GitOps progression. |
| Host and identity | Private build VM; operator current-shell Bastion session and management-local probes. |
| Time | Estimate: 60–90 minutes active plus 5–20 minutes bounded fault/rollout waits. |
| Cost | No new services/nodes; foundation continues billing during pauses. |
| Changes | Incident operator temporarily changes only API scheduling; restores selector and verifies handoff. |
| Exit and re-entry | Six Ready nodes, original replicas and repaired probes; after interruption repair the exact selector before collecting exit evidence. |

Use the [session, evidence and cost operating contract](../docs/operations.md) and [stage map](../docs/state.md). Times are planning estimates, not measured results.

## Prerequisites and resource boundary

Complete [lab 5](05-acr-and-workloads.md) with healthy API/worker rollouts. Run Bash at the repository root on the private build VM, in the active TLS-verified Bastion session. Use only the dedicated lab environment and synthetic workload.

This lab performs **one controlled application scheduling fault**. It does not delete nodes, resize pools, drain/upgrade the cluster, change identity roles or add Azure services. Do not run the fault after GitOps adoption or on a progressed environment. Keep the default pool counts, version and networking intact.

## Directives

### 1. Establish a baseline and a diagnostic order

**Task:** prove health before injecting anything, then identify which evidence belongs to Azure, Kubernetes and the application.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
kubectl config current-context
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{state:provisioningState,version:kubernetesVersion,power:powerState}' -o json
az monitor activity-log list --resource-id "$(output_value clusterId)" --offset 1h \
  --query '[].{time:eventTimestamp,operation:operationName.value,status:status.value}' -o table
kubectl --request-timeout=30s get nodes
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
kubectl get deployment order-api -n orders -o json |
  jq -e '.spec.template.spec.nodeSelector.agentpool == "apps"'
```

Repeat lab 5's port-forward/probes before continuing. If baseline checks fail, recover that existing problem first; do not add another fault.

Start triage with the exact time, operation and affected path. ARM deployment/activity logs explain Azure resource changes; node conditions and pod events explain Kubernetes state; container logs explain runtime behavior; user-path probes explain observed service impact. "No events" is not proof of health.

Activity-log access is a separate Azure permission. A query failure must be diagnosed or escalated, not silently treated as an empty successful result.

</details>

### 2. Inject a selector failure and recover automatically

**Task:** select a nonexistent pool, capture the failed rollout and scheduler explanation, then restore `apps`. A still-serving old replica is not proof that the rollout succeeded.

<details>
<summary>Solution</summary>

The subshell below confines its exit trap to the exercise. It restores the selector and waits for the repaired rollout even if a diagnostic check fails. The controlled denial must show both a rollout timeout and an unschedulable pod selecting `missing`; unrelated API failures are not accepted as success.

```bash
(
  set -euo pipefail
  restore_selector() {
    kubectl patch deployment order-api -n orders --type merge \
      -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"apps"}}}}}' &&
      kubectl rollout status deployment/order-api -n orders --timeout=300s
  }
  trap 'ExerciseStatus=$?; trap - EXIT
    if ! restore_selector; then
      printf "%s\n" "Recovery failed: restore agentpool=apps and verify rollout before continuing." >&2
      exit 1
    fi
    exit "$ExerciseStatus"' EXIT
  kubectl patch deployment order-api -n orders --type merge \
    -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"missing"}}}}}'
  Status=0
  Rollout=$(kubectl rollout status deployment/order-api -n orders --timeout=30s 2>&1) || Status=$?
  printf '%s\n' "$Rollout"
  [[ "$Status" != 0 ]] || { printf '%s\n' 'Unexpected rollout success.' >&2; exit 1; }
  grep -F 'timed out waiting for the condition' <<< "$Rollout"
  kubectl get pods -n orders -l app=order-api -o json | jq -e '
    any(.items[];
      .spec.nodeSelector.agentpool == "missing"
      and any(.status.conditions[]?;
        .type == "PodScheduled" and .status == "False" and .reason == "Unschedulable"
        and (.message | test("node affinity/selector|node selector"; "i"))))'
  kubectl get events -n orders --sort-by=.lastTimestamp
  kubectl describe deployment order-api -n orders
)
```

Expected: new pods cannot match a node; older available replicas can remain. The `apps` pool still exists and has capacity, but cannot satisfy `agentpool=missing`. Adding an arbitrary VM, granting Owner or restarting the queue cannot fix this.

The exit trap cannot survive a lost host or forced process kill. If the terminal dies or recovery fails, reconnect safely and run the explicit repair:

```bash
kubectl patch deployment order-api -n orders --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"apps"}}}}}'
kubectl rollout status deployment/order-api -n orders --timeout=300s
```

If the expected evidence check fails, retain the error and investigate after recovery. Do not interpret a generic timeout as a demonstrated scheduling fault.

</details>

### 3. Make a lifecycle decision without performing an upgrade

**Task:** distinguish Kubernetes version upgrades, node-image updates and maintenance scheduling. Identify a supported next action and its prerequisites.

<details>
<summary>Solution</summary>

```bash
az aks get-upgrades -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o table
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{pool:name,version:orchestratorVersion,image:nodeImageVersion,surge:upgradeSettings.maxSurge}' -o json
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{version:kubernetesVersion,automatic:autoUpgradeProfile}' -o json
az aks maintenanceconfiguration list -g "$(lab_value ResourceGroup)" \
  --cluster-name "$(lab_value ClusterName)" -o table
```

No configured maintenance window is a finding to record, not a reason to invent a default. Discovery of a target is not approval to upgrade.

Your runbook should require supported target/API compatibility, add-on compatibility, node image/OS support, surge quota/IPs, disruption budgets, a backup/restore strategy where state exists, an approved window and user-impact measurement. Kubernetes version and node-image updates are distinct operations. A schedule does not itself prove an update ran successfully.

Leave the cluster unchanged. Actual upgrade/eviction exercises follow durable-recovery preparation in lab 22. Do not patch the node OS manually over SSH or attempt an unsupported downgrade as a rollback plan.

</details>

### 4. Assemble the handoff evidence

**Task:** prove the repaired baseline and preserve the state that later identity, networking and GitOps labs will build on.

<details>
<summary>Solution</summary>

Use this block again for [lab 10's beginner resume path](10-baseline.md#entry-paths). First load the approved settings and verify the active tunnel's TLS/cluster identity with [lab 3 task 3](03-access-and-identity.md#3-verify-api-connectivity-tls-and-authorization); do not create a new session over an already active one.

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az deployment group show -g "$(lab_value ResourceGroup)" -n foundation \
  --query properties.provisioningState -o json | jq -e '. == "Succeeded"'
test -s ./rendered/base/kustomization.yaml
kubectl kustomize ./rendered/base >/dev/null
kubectl get nodes -o json | jq -e '
  (.items | length) == 6
  and ([.items[] | select(.metadata.labels.agentpool == "system")] | length) == 3
  and ([.items[] | select(.metadata.labels.agentpool == "apps")] | length) == 3
  and all(.items[]; any(.status.conditions[]?; .type == "Ready" and .status == "True"))'
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
kubectl get deployments -n orders -o json | jq -e '
  [.items[] | select(.metadata.name == "order-api" or .metadata.name == "order-worker")] as $apps |
  ($apps | length) == 2
  and all($apps[];
    .spec.template.spec.nodeSelector.agentpool == "apps"
    and .spec.replicas == (if .metadata.name == "order-api" then 2 else 1 end)
    and .status.observedGeneration == .metadata.generation
    and .status.updatedReplicas == .spec.replicas
    and .status.availableReplicas == .spec.replicas)'
kubectl get pods -n orders -o wide
```

Restart lab 5's local port-forward if its selected pod was replaced, then:

```bash
curl --fail --show-error --max-time 10 http://127.0.0.1:8080/healthz
curl --fail --show-error --max-time 10 http://127.0.0.1:8080/readyz
```

Require all checks, including the final repaired rollout and probes. Retain a private worksheet with foundation identity/state, version/extension evidence, pools/zones, image digest, original/failed/recovered state and lifecycle/cost decisions. Do not save tokens, raw kubeconfigs or private keys.

These checks verify the beginner baseline, not an automatic stage detector. A missing file or a different replica count on a progressed cluster does not authorize rerendering or resetting its configuration.

</details>

## Exit evidence and transition

All beginner exit checks must pass. Preserve settings, public keys, the image, healthy API/worker and `rendered/base`. There must be no residual injected selector or accidental node/version/network change.

Continue to **[lab 10](10-baseline.md#entry-paths), beginner resume path**, for the architecture/trade-off review; do not repeat deployment/build/render/fault steps already evidenced. Then continue to lab 11. Numbers 7-9 are intentionally unused.

## Customer conversation

<details>
<summary>Model answer: Does an available old pod mean our deployment succeeded?</summary>

No. A rollout can stall while old replicas serve traffic. Require the desired generation and updated/available replicas, plus the intended user-path checks, rather than a single green pod.

</details>

<details>
<summary>Model answer: Does closing Bastion or stopping AKS eliminate the bill?</summary>

No. Disconnecting stops a local session, not Azure resources. AKS stop/start has support and operational constraints and does not remove disks or dependencies. Bastion, Firewall, Premium services, storage and other retained resources can keep billing. Choose a supported pause or deliberate teardown based on the complete inventory.

</details>

## Cleanup, pause and resume

If any fault remains, repair it before pausing. Stop attached port-forwards with Ctrl+C and run `disconnect_aks_bastion` in the owner shell. Retain only the intended local artifacts. Reconnect using lab 3 and verify context on return.

To pause build-VM compute, close sessions first, then use the [deallocation/start recipe](../docs/operations.md#pausing-resuming-and-final-cleanup) from the original workstation. Guest shutdown is not Azure deallocation; managed disks, Firewall and Bastion still bill. Do not stop/resize the AKS pools as part of this lab.

If permanently leaving after beginners, follow [beginner-only teardown](../docs/operations.md#beginner-only-teardown). Do not run Fleet, backup or database commands for resources not introduced yet, and do not delete anything if continuing to intermediate labs.

## References

[AKS troubleshooting](https://learn.microsoft.com/azure/aks/troubleshooting), [AKS lifecycle best practices](https://learn.microsoft.com/azure/aks/operator-best-practices-cluster-security), [AKS upgrades](https://learn.microsoft.com/azure/aks/upgrade-cluster), [node-image upgrades](https://learn.microsoft.com/azure/aks/node-image-upgrade), [AKS stop/start](https://learn.microsoft.com/azure/aks/start-stop-cluster). This lab inspects lifecycle choices; it does not prove an actual upgrade or a backup/restore.

---

**Next lab:** [Lab 10 - Enterprise baseline, beginner resume path](10-baseline.md#entry-paths). Reuse the existing foundation; do not redeploy or rerender.
