# Lab 9 — Maintain AKS while measuring the business effect

**Customer:** regular lifecycle work without promising zero disruption. **Time:** 3–5 hours with a supported target available. **Result:** a real blocked drain and repair, a supported Kubernetes upgrade, a node-image update, and measured traffic/transaction evidence.

Prerequisites: labs 1–8, last successful backup and PostgreSQL restore test, spare vCPU/subnet quota for surge, and an approved change window. A customer SLO is not an AKS control-plane SLA. Run from the private PowerShell 7 management host; use Contributor on AKS plus scoped node administration, not admin kubeconfig.

## 1. Build a specific go/no-go record

```powershell
. .\scripts\Use-Lab.ps1
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
New-Item .\.artifacts\advanced -ItemType Directory -Force | Out-Null
$AppKustomization = 'orders'
$FluxNamespace = 'flux-system'
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName -o json | Set-Content .\.artifacts\advanced\cluster-before-upgrade.json
az aks get-upgrades -g $Lab.ResourceGroup -n $Lab.ClusterName -o json | Set-Content .\.artifacts\advanced\available-upgrades.json
az aks get-versions -l $Lab.Location -o table
az vm list-usage -l $Lab.Location -o table
az aks nodepool list -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -o table
kubectl get nodes -o wide
kubectl get pdb,hpa -A
kubectl get events -A --field-selector type=Warning
flux get kustomizations -A
Get-Content .\.artifacts\advanced\available-upgrades.json
```

Select an **explicit GA** version from `get-upgrades`, not a blog or today's default:

```powershell
$Available = Get-Content .\.artifacts\advanced\available-upgrades.json -Raw | ConvertFrom-Json
$Allowed = @($Available.controlPlaneProfile.upgrades | Where-Object { -not $_.isPreview } | ForEach-Object kubernetesVersion)
$Target = Read-Host "Approved target from: $($Allowed -join ', ')"
if ($Target -notin $Allowed) { throw 'Target is not an advertised supported non-preview upgrade.' }
```

If the list is empty, **defer the Kubernetes-version portion** until a supported update exists; node-image-only work does not prove a Kubernetes upgrade. Do not downgrade or provision an unsupported release to manufacture an exercise.

Review the target release notes, Kubernetes API removals, AzureLinux3 support, CSI/Backup extension compatibility, Cilium/network policies, Flux, KEDA, gateway, monitoring and Defender. `kubectl api-resources` tells you served APIs, not whether clients still call deprecated endpoints. Review API-server audit logs from lab 5, API deprecation insights/upgrade checks, and every CRD/webhook vendor's supported matrix. Save this decision with owner and date.

```powershell
kubectl api-resources -o wide | Set-Content .\.artifacts\advanced\served-apis.txt
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o yaml | Set-Content .\.artifacts\advanced\webhooks-before.yaml
az aks nodepool get-upgrades -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps -o json
```

No-go conditions: failing admission webhooks, Pending production pods, zero allowed disruptions without a scaling plan, unsupported extension, no surge quota, backup not verified, ongoing Fleet/autoupgrade, or an unbounded consumer backlog. Fix those first.

## 2. Separate maintenance schedules from execution ownership

Record existing upgrade channels; disable only automatic Kubernetes scheduling for this manual exercise, and retain a node OS channel:

```powershell
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --auto-upgrade-channel none --node-os-upgrade-channel NodeImage
az aks maintenanceconfiguration add -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName `
  -n aksManagedAutoUpgradeSchedule --schedule-type Weekly --day-of-week Saturday `
  --interval-weeks 1 --duration 4 --utc-offset +00:00 --start-time 01:00
az aks maintenanceconfiguration add -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName `
  -n aksManagedNodeOSUpgradeSchedule --schedule-type Weekly --day-of-week Sunday `
  --interval-weeks 1 --duration 4 --utc-offset +00:00 --start-time 01:00
az aks maintenanceconfiguration list -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName
```

Use `update` instead of `add` if those names already exist. Schedules do not enable upgrades; auto-upgrade and node OS channels are distinct. The `default` maintenance configuration is for AKS platform releases, not a substitute for these schedules. Planned maintenance is best effort, and urgent platform maintenance can occur outside windows. Manual upgrade commands are explicit change actions; do not assume the schedule delays your command.

Configure bounded surge and drain timeouts on both managed pools:

```powershell
'system','apps' | ForEach-Object {
  az aks nodepool update -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n $_ `
    --max-surge 1 --drain-timeout 30 --node-soak-duration 5
}
```

Inspect actual pool names and substitute if foundation used a different system-pool name. One extra node **per updating pool**, SKU availability, zone capacity, pod scheduling constraints and IP planning all affect feasibility. Do not use `--force` to bypass failed upgrade validations.

## 3. Deliberately block a real eviction without touching the orders PDB

The standalone `maintenance-lab` namespace is explicitly incident-owned, not selected by a Flux Kustomization. Its single-replica deployment with `minAvailable: 1` is guaranteed to block voluntary eviction once Ready.

```powershell
kubectl apply -f .\advanced\maintenance\drain.yaml
kubectl rollout status deployment/drain-guard -n maintenance-lab --timeout=300s
$Node = kubectl get pod -n maintenance-lab -l app=drain-guard -o jsonpath='{.items[0].spec.nodeName}'
kubectl get pdb drain-guard -n maintenance-lab
$PSNativeCommandUseErrorActionPreference = $false
try {
  kubectl drain $Node --ignore-daemonsets --pod-selector=app=drain-guard --timeout=60s
  if ($LASTEXITCODE -eq 0) { throw 'Expected PDB block did not occur; inspect the test before continuing.' }
} finally {
  kubectl uncordon $Node
  $PSNativeCommandUseErrorActionPreference = $true
}
kubectl describe pdb drain-guard -n maintenance-lab
```

Expected: `Cannot evict pod as it would violate the pod's disruption budget`, allowed disruptions 0, timeout. The selector limits evictions to the test pod; drain still cordons the node, so `finally` always uncordons it. This is a **real Kubernetes eviction API call**, not a fake text error.

Solution: restore spare capacity, not remove the PDB:

```powershell
kubectl scale deployment drain-guard -n maintenance-lab --replicas=2
kubectl rollout status deployment/drain-guard -n maintenance-lab --timeout=300s
kubectl get pdb drain-guard -n maintenance-lab
try {
  kubectl drain $Node --ignore-daemonsets --pod-selector=app=drain-guard --timeout=300s
} finally { kubectl uncordon $Node }
kubectl delete namespace maintenance-lab
```

Expected at least one allowed disruption and successful eviction. If the second pod cannot schedule, fix capacity/taints/selectors first. `--disable-eviction`, deleting the PDB and force-deleting pods are not acceptable fixes. PDBs constrain **voluntary** evictions; they do not prevent node/zone failure.

## 4. Run traffic and perform a supported upgrade

Use two PowerShell terminals on the management host. In terminal A:

```powershell
$AppUrl = Read-Host 'Lab 3 HTTPS application base URL'
.\advanced\Measure-Orders.ps1 -BaseUri $AppUrl -Seconds 3600 -OutputPath .\.artifacts\advanced\upgrade-traffic.json
```

The helper measures readiness HTTP status and latency once per second. It is **not** a full order SLI. In addition, create a synthetic order immediately before and after each operation and check GET plus worker/database evidence as in lab 8. Record retry outcomes separately; retrying should not hide failed requests in the SLI. Agree the lab objective first, for example ≥99% successful probes and no loss of accepted test orders; use the customer's actual SLO for a real change.

In terminal B, save your target and start the supported control-plane upgrade, then node pools one at a time:

```powershell
$ChangeStart = [DateTime]::UtcNow
az aks upgrade -g $Lab.ResourceGroup -n $Lab.ClusterName --kubernetes-version $Target --control-plane-only --yes
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query '{version:kubernetesVersion,state:provisioningState}'
az aks nodepool upgrade -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n system --kubernetes-version $Target
kubectl get nodes -o wide
kubectl get pods -n kube-system
az aks nodepool upgrade -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps --kubernetes-version $Target
kubectl get nodes -o wide
kubectl rollout status deployment/order-api -n orders --timeout=600s
kubectl rollout status deployment/order-worker -n orders --timeout=600s
```

Do not leave skewed versions indefinitely. AKS advertises and enforces its supported skew; upgrading control plane first is intentional, skipping unsupported minors is not. If an operation fails, capture ARM error, pool provisioning state, eviction events, pending pod reasons and quota. Repair the cause and **retry the same supported target**; do not start an unrelated update or manually edit VMSS nodes.

Before moving to the next pool, require healthy system pods, ready production replicas and successful order processing. Stop on SLO breach and follow section 6; do not mistake a sequential command list for an approval system.

## 5. Exercise node-image maintenance separately and validate

```powershell
az aks nodepool list -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName `
  --query '[].{name:name,kubernetes:orchestratorVersion,image:nodeImageVersion,state:provisioningState}' -o json |
  Set-Content .\.artifacts\advanced\images-before.json
az aks nodepool upgrade -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName -n apps --node-image-only
az aks nodepool list -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName `
  --query '[].{name:name,kubernetes:orchestratorVersion,image:nodeImageVersion,state:provisioningState}' -o json |
  Set-Content .\.artifacts\advanced\images-after.json
kubectl get nodes -o wide
kubectl get pdb -n orders
flux get kustomizations -A
kubectl get events -A --field-selector type=Warning
```

If the version upgrade already installed the newest node image, image-only may be a no-op. Record that honestly and schedule a subsequent image release to demonstrate an actual image replacement. Do not claim changed nodes from a command's exit code alone.

Update the authoritative foundation **deployment parameters** to `$Target` and pool maintenance settings before future IaC deployment. Do not reapply an old version from lab 1. Revisit pinned PSA minor labels in team Git configuration only after testing new requirements. Preserve SHA and version/image evidence.

When terminal A finishes:

```powershell
$Samples = Get-Content .\.artifacts\advanced\upgrade-traffic.json -Raw | ConvertFrom-Json
$Failed = @($Samples | Where-Object status -NE 200)
[pscustomobject]@{Samples=$Samples.Count;Failed=$Failed.Count;Start=$ChangeStart;End=[DateTime]::UtcNow}
$Failed | Format-Table
```

Correlate failed/slow samples with node drain timestamps, ingress endpoints, order retries and queue age. Report readiness and order SLI separately. Investigate all missing IDs against PostgreSQL and Service Bus active/dead-letter counts.

## 6. Practice application rollback; choose cluster recovery honestly

In the lab-4 Git repository, revert the known **application** release commit from that lab, not the database migration or platform upgrade:

```powershell
git log --oneline -12
$AppReleaseCommit = Read-Host 'Reviewed application-only release commit to revert'
git show --stat $AppReleaseCommit
git revert --no-commit $AppReleaseCommit
.\advanced\Publish-ReviewedChange.ps1 -Message "Revert the reviewed application-only release"
flux reconcile kustomization $AppKustomization -n $FluxNamespace --with-source
kubectl rollout status deployment/order-api -n orders --timeout=600s
```

Check schema backward compatibility before reverting code. If there is no safe app-only release to revert, use the lab-4 tested release rollback exercise first; **do not** revert the lab-8 integration arbitrarily. Verify an accepted order after rollback, then re-promote the approved known-good release through Git for lab 10.

AKS Kubernetes downgrade is not supported. If cluster repair is unsuitable, use **blue/green cluster replacement**: provision a supported cluster and networking, configure identities and private DNS, restore required CSI data into a supported target, reconcile Git at a verified compatible SHA, run read/write checks while traffic is fenced, then change routing. Managed PostgreSQL/Service Bus remain external; do not clone them into two writers accidentally. Retain the old cluster until rollback criteria expire, but do not keep an unsupported cluster as the normal escape route.

## 7. Customer debrief and cleanup

Deliver: before/after versions and image IDs, target eligibility, compatibility/no-go checklist, blocked and successful eviction outputs, ARM operation result, observed traffic/transaction SLI, application rollback SHA, replacement-cluster decision, and remaining unsupported/partial items.

**Model answers**

1. **"Does a 99.95% AKS SLA mean my app meets it?"** No. Control-plane availability is not ingress, database, dependency, capacity or application availability.
2. **"Can maintenance windows guarantee no daytime change?"** No; they coordinate eligible scheduled operations, are best effort, and urgent service maintenance is an exception. Manual/Fleet work needs an explicit operating model.
3. **"Do zones solve region loss?"** No. They address failures inside one region; shared regional dependencies and operational errors still matter.
4. **"Should we buy LTS?"** Evaluate Premium tier/LTS support and application compatibility against your maintenance capability. Extended support is not a substitute for node-image patching, tested upgrade cadence or deprecation remediation.

Remove the incident namespace if any step stopped early and uncordon only the node recorded in `$Node`. Keep upgraded versions, PostgreSQL, backup, application and telemetry for lab 10. Restore the previous auto-upgrade channel **only when it has a single agreed owner**; lab 10 uses Fleet and must not compete with autonomous updates. Remove no pools, PVCs, identities or regional resources in this lab.

## Official references and status

Checked **2026-09-10**: [upgrade AKS](https://learn.microsoft.com/azure/aks/upgrade-cluster), [planned maintenance](https://learn.microsoft.com/azure/aks/planned-maintenance), [node-image upgrade](https://learn.microsoft.com/azure/aks/node-image-upgrade), [upgrade options](https://learn.microsoft.com/azure/aks/upgrade-aks-cluster), [supported versions](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions). Required commands use GA update/drain features; no preview force-upgrade, preview drain bypass or Kubernetes downgrade is part of the recovery plan.
