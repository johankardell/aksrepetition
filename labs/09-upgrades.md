# Lab 9 — Maintain AKS while measuring the business effect

**Customer:** regular lifecycle work without promising zero disruption. **Time:** 3–5 hours with a supported target available. **Result:** a real blocked drain and repair, a supported Kubernetes upgrade, a node-image update, and measured traffic/transaction evidence.

Prerequisites: labs 1–8, last successful backup and PostgreSQL restore test, spare vCPU/subnet quota for surge, and an approved change window. A customer SLO is not an AKS control-plane SLA. Run Bash from the repository root on the private Linux management host with Azure CLI, kubectl, Flux CLI, Git, GitHub CLI, `jq`, and the Linux HTTP/database tools from lab 8. Start each terminal with `set -euo pipefail`; session variables do not transfer between terminals. Use Contributor on AKS plus scoped node administration, not admin kubeconfig.

## 1. Build a specific go/no-go record

**Task:** choose a currently advertised GA upgrade target and document compatibility, capacity, recovery readiness and blockers before authorizing the change. If no supported target exists, defer the version-upgrade portion; do not manufacture one by downgrading or selecting an unsupported release.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
mkdir -p .artifacts/advanced
AppKustomization=orders
FluxNamespace=flux-system
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o json > .artifacts/advanced/cluster-before-upgrade.json
az aks get-upgrades -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o json > .artifacts/advanced/available-upgrades.json
az aks get-versions -l "$(lab_value Location)" -o table
az vm list-usage -l "$(lab_value Location)" -o table
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -o table
kubectl get nodes -o wide
kubectl get pdb,hpa -A
kubectl get events -A --field-selector type=Warning
flux get kustomizations -A
jq . .artifacts/advanced/available-upgrades.json
```

Select an **explicit GA** version from `get-upgrades`, not a blog or today's default:

```bash
Available=$(< .artifacts/advanced/available-upgrades.json)
Allowed=$(jq -c '[.controlPlaneProfile.upgrades[]? | select(.isPreview != true) | .kubernetesVersion]' <<< "$Available")
[[ "$(jq 'length' <<< "$Allowed")" != 0 ]] || {
  printf '%s\n' 'No supported non-preview target; defer the Kubernetes-version portion.' >&2; exit 1;
}
printf 'Advertised GA targets: %s\n' "$(jq -r 'join(", ")' <<< "$Allowed")"
read -r -p 'Approved target: ' Target
jq -e --arg target "$Target" 'index($target) != null' <<< "$Allowed" > /dev/null || {
  printf '%s\n' 'Target is not an advertised supported non-preview upgrade.' >&2; exit 1;
}
```

If the list is empty, **defer the Kubernetes-version portion** until a supported update exists; node-image-only work does not prove a Kubernetes upgrade. Do not downgrade or provision an unsupported release to manufacture an exercise.

Review the target release notes, Kubernetes API removals, AzureLinux3 support, CSI/Backup extension compatibility, Cilium/network policies, Flux, KEDA, gateway, monitoring and Defender. `kubectl api-resources` tells you served APIs, not whether clients still call deprecated endpoints. Review API-server audit logs from lab 5, API deprecation insights/upgrade checks, and every CRD/webhook vendor's supported matrix. Save this decision with owner and date.

```bash
kubectl api-resources -o wide > .artifacts/advanced/served-apis.txt
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o yaml > .artifacts/advanced/webhooks-before.yaml
az aks nodepool get-upgrades -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n apps -o json
```

No-go conditions: failing admission webhooks, Pending production pods, zero allowed disruptions without a scaling plan, unsupported extension, no surge quota, backup not verified, ongoing Fleet/autoupgrade, or an unbounded consumer backlog. Fix those first.

Use a dated decision record with current/target versions, the advertised upgrade entry, client skew, each add-on's supported target, deprecated API findings, per-pool surge capacity, PDB readiness, last successful restore evidence, queue baseline, change owner and abort criteria. Approve only when every required item has evidence. For example, a target being advertised with an unverified Backup extension is **no-go**, not partial approval; record the compatibility owner and defer execution. Actual versions and outcomes must come from your subscription.

</details>

## 2. Separate maintenance schedules from execution ownership

**Task:** record existing channels, establish noncompeting maintenance ownership, and configure bounded per-pool surge/drain settings. Execute manual changes only in the approved window; a maintenance schedule does not postpone a manual command or guarantee capacity.

<details>
<summary>Solution</summary>

Record existing upgrade channels; disable only automatic Kubernetes scheduling for this manual exercise, and retain a node OS channel:

```bash
az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --auto-upgrade-channel none --node-os-upgrade-channel NodeImage
az aks maintenanceconfiguration add -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  -n aksManagedAutoUpgradeSchedule --schedule-type Weekly --day-of-week Saturday \
  --interval-weeks 1 --duration 4 --utc-offset +00:00 --start-time 01:00
az aks maintenanceconfiguration add -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  -n aksManagedNodeOSUpgradeSchedule --schedule-type Weekly --day-of-week Sunday \
  --interval-weeks 1 --duration 4 --utc-offset +00:00 --start-time 01:00
az aks maintenanceconfiguration list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)"
```

Use `update` instead of `add` if those names already exist. Schedules do not enable upgrades; auto-upgrade and node OS channels are distinct. The `default` maintenance configuration is for AKS platform releases, not a substitute for these schedules. Planned maintenance is best effort, and urgent platform maintenance can occur outside windows. Manual upgrade commands are explicit change actions; do not assume the schedule delays your command.

Configure bounded surge and drain timeouts on both managed pools:

```bash
for Pool in system apps; do
  az aks nodepool update -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n "$Pool" \
    --max-surge 1 --drain-timeout 30 --node-soak-duration 5
done
```

Inspect actual pool names and substitute if foundation used a different system-pool name. One extra node **per updating pool**, SKU availability, zone capacity, pod scheduling constraints and IP planning all affect feasibility. Do not use `--force` to bypass failed upgrade validations.

</details>

## 3. Deliberately block a real eviction without touching the orders PDB

The standalone `maintenance-lab` namespace is explicitly incident-owned, not selected by a Flux Kustomization. Its single-replica deployment with `minAvailable: 1` is guaranteed to block voluntary eviction once Ready.

**Task:** demonstrate a real PDB-blocked drain and repair it by restoring spare capacity. Limit evictions to the incident workload, always uncordon the recorded node, and do not bypass the eviction API or remove the PDB.

<details>
<summary>Solution</summary>

```bash
kubectl apply -f ./advanced/maintenance/drain.yaml
kubectl rollout status deployment/drain-guard -n maintenance-lab --timeout=300s
Node=$(kubectl get pod -n maintenance-lab -l app=drain-guard -o 'jsonpath={.items[0].spec.nodeName}')
[[ -n "$Node" ]] || { printf '%s\n' 'No incident node was recorded.' >&2; exit 1; }
printf '%s\n' "$Node" > .artifacts/advanced/drain-node.txt
kubectl get pdb drain-guard -n maintenance-lab
(
  set -euo pipefail
  trap 'Status=$?; kubectl uncordon "$Node" || Status=$?; exit "$Status"' EXIT
  DrainStatus=0
  kubectl drain "$Node" --ignore-daemonsets --pod-selector=app=drain-guard --timeout=60s \
    > .artifacts/advanced/blocked-drain.txt 2>&1 || DrainStatus=$?
  cat .artifacts/advanced/blocked-drain.txt
  [[ "$DrainStatus" != 0 ]] || { printf '%s\n' 'Expected PDB block did not occur; inspect the test.' >&2; exit 1; }
  grep -Fi 'disruption budget' .artifacts/advanced/blocked-drain.txt
)
kubectl describe pdb drain-guard -n maintenance-lab
```

Expected: `Cannot evict pod as it would violate the pod's disruption budget`, allowed disruptions 0, timeout. The selector limits evictions to the test pod; drain still cordons the node, so the subshell's `EXIT` trap always attempts to uncordon it and reports any recovery failure. After host loss or forced termination, recover the exact node from `.artifacts/advanced/drain-node.txt` and uncordon it first. This is a **real Kubernetes eviction API call**, not a fake text error.

Solution: restore spare capacity, not remove the PDB:

```bash
kubectl scale deployment drain-guard -n maintenance-lab --replicas=2
kubectl rollout status deployment/drain-guard -n maintenance-lab --timeout=300s
kubectl get pdb drain-guard -n maintenance-lab
(
  set -euo pipefail
  trap 'Status=$?; kubectl uncordon "$Node" || Status=$?; exit "$Status"' EXIT
  kubectl drain "$Node" --ignore-daemonsets --pod-selector=app=drain-guard --timeout=300s
)
kubectl delete namespace maintenance-lab
```

Expected at least one allowed disruption and successful eviction. If the second pod cannot schedule, fix capacity/taints/selectors first. `--disable-eviction`, deleting the PDB and force-deleting pods are not acceptable fixes. PDBs constrain **voluntary** evictions; they do not prevent node/zone failure.

</details>

## 4. Run traffic and perform a supported upgrade

**Task:** measure readiness and durable order processing during an approved control-plane-first, pool-by-pool upgrade. Require healthy workloads and business processing before each next pool; stop on an SLO breach. Do not use forced validation bypasses or attempt Kubernetes downgrade.

<details>
<summary>Solution</summary>

Use two Bash terminals on the management host. In terminal A:

```bash
set -euo pipefail
source ./scripts/use-lab.sh
read -r -p 'Lab 3 HTTPS application base URL: ' AppUrl
AppUrl=${AppUrl%/}
[[ "$AppUrl" == https://* ]] || { printf '%s\n' 'Use the trusted HTTPS application URL.' >&2; exit 1; }
[[ -r "$Root/rendered/certs/lab-ca-bundle.pem" ]] || { printf '%s\n' 'Restore the approved lab-3 CA bundle on this host first.' >&2; exit 1; }
export SSL_CERT_FILE="$Root/rendered/certs/lab-ca-bundle.pem" CURL_CA_BUNDLE="$Root/rendered/certs/lab-ca-bundle.pem"
bash ./advanced/measure-orders.sh --base-uri "$AppUrl" --seconds 3600 --output-path .artifacts/advanced/upgrade-traffic.json
```

The helper measures sequential readiness HTTP status and latency, waiting one second after each probe; slow requests or the ten-second request timeout reduce the sampling frequency. It is **not** a full order SLI. In addition, create a synthetic order immediately before and after each operation and check GET plus worker/database evidence as in lab 8. Record retry outcomes separately; retrying should not hide failed requests in the SLI. Agree the lab objective first, for example ≥99% successful probes and no loss of accepted test orders; use the customer's actual SLO for a real change.

In terminal B, save your target and start the supported control-plane upgrade, then node pools one at a time:

Use the existing session from tasks 1–3 as terminal B so `$Target`, `$Lab` and the Flux names are defined. In a fresh terminal, run `set -euo pipefail`, source `./scripts/use-lab.sh` and repeat target discovery/validation rather than guessing or relying on variables from terminal A. Execute the following operations individually, applying the health gate between them.

```bash
ChangeStart=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '%s\n' "$ChangeStart" > .artifacts/advanced/upgrade-start.txt
az aks upgrade -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --kubernetes-version "$Target" --control-plane-only --yes
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --query '{version:kubernetesVersion,state:provisioningState}'
```

Apply the health/business gate before upgrading the system pool:

```bash
az aks nodepool upgrade -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n system --kubernetes-version "$Target"
kubectl get nodes -o wide
kubectl get pods -n kube-system
```

Apply the same gate again before upgrading the apps pool:

```bash
az aks nodepool upgrade -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n apps --kubernetes-version "$Target"
kubectl get nodes -o wide
kubectl rollout status deployment/order-api -n orders --timeout=600s
kubectl rollout status deployment/order-worker -n orders --timeout=600s
```

Do not leave skewed versions indefinitely. AKS advertises and enforces its supported skew; upgrading control plane first is intentional, skipping unsupported minors is not. If an operation fails, capture ARM error, pool provisioning state, eviction events, pending pod reasons and quota. Repair the cause and **retry the same supported target**; do not start an unrelated update or manually edit VMSS nodes.

Before moving to the next pool, require healthy system pods, ready production replicas and successful order processing. Stop on SLO breach and follow section 6; do not mistake a sequential command list for an approval system.

</details>

## 5. Exercise node-image maintenance separately and validate

**Task:** compare node image IDs before/after image-only maintenance, calculate observed readiness availability separately from order outcomes, and update authoritative version/PSA configuration. Report a no-op honestly; do not replay the original bootstrap over the progressed cluster.

<details>
<summary>Solution</summary>

```bash
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{name:name,kubernetes:orchestratorVersion,image:nodeImageVersion,state:provisioningState}' -o json \
  > .artifacts/advanced/images-before.json
az aks nodepool upgrade -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" -n apps --node-image-only
az aks nodepool list -g "$(lab_value ResourceGroup)" --cluster-name "$(lab_value ClusterName)" \
  --query '[].{name:name,kubernetes:orchestratorVersion,image:nodeImageVersion,state:provisioningState}' -o json \
  > .artifacts/advanced/images-after.json
kubectl get nodes -o wide
kubectl get pdb -n orders
flux get kustomizations -A
kubectl get events -A --field-selector type=Warning
```

If the version upgrade already installed the newest node image, image-only may be a no-op. Record that honestly and schedule a subsequent image release to demonstrate an actual image replacement. Do not claim changed nodes from a command's exit code alone.

Update the authoritative foundation **deployment parameters** to `$Target` and pool maintenance settings before future IaC deployment. Do not reapply an old version from lab 1. Revisit pinned PSA minor labels in team Git configuration only after testing new requirements. Preserve SHA and version/image evidence.

After the last maintenance operation, record the change end in terminal B. When terminal A finishes, calculate availability over its actual sample population and report that population's timestamps separately from the change window:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ > .artifacts/advanced/upgrade-end.txt
# Wait for terminal A to finish writing its evidence before the following commands.
Samples=$(< .artifacts/advanced/upgrade-traffic.json)
jq -e 'type == "array" and length > 0' <<< "$Samples" > /dev/null || {
  printf '%s\n' 'No traffic sample array was recorded; availability cannot be calculated.' >&2; exit 1;
}
read -r ChangeStart < .artifacts/advanced/upgrade-start.txt
read -r ChangeEnd < .artifacts/advanced/upgrade-end.txt
jq --arg changeStart "$ChangeStart" --arg changeEnd "$ChangeEnd" '
  length as $count | (map(select(.status != 200)) | length) as $failed |
  {Samples:$count, Failed:$failed,
   AvailabilityPercent:((100 * ($count - $failed) / $count * 1000 | round) / 1000),
   Start:.[0].utc, End:.[-1].utc,
   ChangeStart:$changeStart, ChangeEnd:$changeEnd}' <<< "$Samples"
jq '[.[] | select(.status != 200)]' <<< "$Samples"
```

This summary covers the entire probe run, including baseline/recovery observations; it is not an isolated maintenance-only percentage. Verify that probing covered every operation. If the one-hour run ended before maintenance did, record the uncovered interval as incomplete rather than claiming whole-change availability. Correlate failed/slow samples with node drain timestamps, ingress endpoints, order retries and queue age. Report readiness and order SLI separately. Investigate all missing IDs against PostgreSQL and Service Bus active/dead-letter counts.

For example, 10 failed observations out of 1,000 means 99% sampled readiness availability; it says nothing by itself about the durability of accepted orders. This is an illustrative calculation, not a measured lab result. Pair the actual sample count and time range with accepted/processed/missing order IDs, latency and retries, and compare against the objective agreed before the change.

Persist the chosen `KubernetesVersion` in local deployment settings without committing those settings. In the owned infrastructure definition, retain the current post-lab networking/add-ons as well as `maxSurge`, `drainTimeoutInMinutes` and `nodeSoakDurationInMinutes`; do not use the old foundation as a rollback. Update all three pinned PSA version labels in `advanced/governance/teams.yaml` to the tested target minor using lab 7's reviewed Git workflow, then reconcile `teams` and record its applied SHA.

</details>

## 6. Practice application rollback; choose cluster recovery honestly

**Task:** roll back a reviewed, schema-compatible application-only release through Git and re-promote the known-good release for lab 10. Explain a replacement-cluster recovery path without a Kubernetes downgrade or two active database writers. If no safe release exists, complete lab 4's rollback exercise first.

<details>
<summary>Solution</summary>

In the lab-4 Git repository, revert the known **application** release commit from that lab, not the database migration or platform upgrade:

```bash
git log --oneline -12
read -r -p 'Reviewed application-only release commit to revert: ' AppReleaseCommit
[[ "$AppReleaseCommit" =~ ^[[:xdigit:]]{7,40}$ ]] || { printf '%s\n' 'Expected a reviewed commit SHA.' >&2; exit 1; }
git --no-pager show --stat "$AppReleaseCommit"
git revert --no-commit "$AppReleaseCommit"
bash ./advanced/publish-reviewed-change.sh --message "Revert the reviewed application-only release"
flux reconcile kustomization "$AppKustomization" -n "$FluxNamespace" --with-source
kubectl rollout status deployment/order-api -n orders --timeout=600s
```

Check schema backward compatibility before reverting code. If there is no safe app-only release to revert, use the lab-4 tested release rollback exercise first; **do not** revert the lab-8 integration arbitrarily. Verify an accepted order after rollback, then re-promote the approved known-good release through Git for lab 10.

AKS Kubernetes downgrade is not supported. If cluster repair is unsuitable, use **blue/green cluster replacement**: provision a supported cluster and networking, configure identities and private DNS, restore required CSI data into a supported target, reconcile Git at a verified compatible SHA, run read/write checks while traffic is fenced, then change routing. Managed PostgreSQL/Service Bus remain external; do not clone them into two writers accidentally. Retain the old cluster until rollback criteria expire, but do not keep an unsupported cluster as the normal escape route.

</details>

## 7. Customer debrief and cleanup

Deliver: before/after versions and image IDs, target eligibility, compatibility/no-go checklist, blocked and successful eviction outputs, ARM operation result, observed traffic/transaction SLI, application rollback SHA, replacement-cluster decision, and remaining unsupported/partial items.

**Task:** answer the customer questions, reconcile the evidence with the agreed objective, and remove incident state without undoing supported upgrades or enabling a competing upgrade owner.

<details>
<summary>Model answer: Does a 99.95% AKS SLA mean my app meets it?</summary>

No. Control-plane availability is not ingress, database, dependency, capacity or application availability.

</details>

<details>
<summary>Model answer: Can maintenance windows guarantee no daytime change?</summary>

No; they coordinate eligible scheduled operations, are best effort, and urgent service maintenance is an exception. Manual/Fleet work needs an explicit operating model.

</details>

<details>
<summary>Model answer: Do zones solve region loss?</summary>

No. They address failures inside one region; shared regional dependencies and operational errors still matter.

</details>

<details>
<summary>Model answer: Should we buy LTS?</summary>

Evaluate Premium tier/LTS support and application compatibility against your maintenance capability. Extended support is not a substitute for node-image patching, tested upgrade cadence or deprecation remediation.

</details>

Remove the incident namespace if any step stopped early and uncordon only the node recorded in `$Node`. Keep upgraded versions, PostgreSQL, backup, application and telemetry for lab 10. Restore the previous auto-upgrade channel **only when it has a single agreed owner**; lab 10 uses Fleet and must not compete with autonomous updates. Remove no pools, PVCs, identities or regional resources in this lab.

<details>
<summary>Solution: close the change and remove incident state</summary>

If the drain exercise stopped early, inspect the recorded node and incident namespace before acting:

```bash
: "${Node:?Recover the exact incident node from .artifacts/advanced/drain-node.txt before cleanup}"
kubectl get node "$Node"
kubectl get all,pdb -n maintenance-lab
kubectl uncordon "$Node"
kubectl delete namespace maintenance-lab --ignore-not-found
flux get kustomizations -A
kubectl get nodes
kubectl get pods,pdb -n orders
```

Only use `$Node` retained from this exercise; if it was lost, identify the incident pod's node from saved evidence instead of uncordoning every node. Confirm no unexpected cordon, stalled rollout or unreconciled release remains. The closeout records both successful operations and deferred work, such as an unavailable Kubernetes target or a node-image no-op. A successful ARM operation does not override missing order IDs or a breached application objective.

</details>

## Official references and status

Checked **2026-09-10**: [upgrade AKS](https://learn.microsoft.com/azure/aks/upgrade-cluster), [planned maintenance](https://learn.microsoft.com/azure/aks/planned-maintenance), [node-image upgrade](https://learn.microsoft.com/azure/aks/node-image-upgrade), [upgrade options](https://learn.microsoft.com/azure/aks/upgrade-aks-cluster), [supported versions](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions). Required commands use GA update/drain features; no preview force-upgrade, preview drain bypass or Kubernetes downgrade is part of the recovery plan.
