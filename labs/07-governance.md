# Lab 7 — Govern a shared platform without confusing the boundaries

**Customer:** two internal teams need delegated delivery, not cluster administration. **Time:** 2–3 hours, including asynchronous policy evaluation. **Result:** attributed authorization, admission and quota denials; a narrow exception with an expiry; Defender coverage evidence.

This lab is cumulative after labs 1–6. Use synthetic data. Namespace tenancy is appropriate for cooperating internal teams, **not a hard boundary for hostile tenants**. Defender pricing is subscription-scoped and incurs charges. Do not enable it in a shared subscription without the subscription owner's approval.

## 1. Establish the operator and GitOps boundaries

All Git changes retain lab 4's protected `main` workflow. Stage only the indicated files, then use `advanced\Publish-ReviewedChange.ps1`: it creates a branch/PR and waits for your normal review and merge before returning to updated `main`. It never approves or merges its own PR. If you stop the helper with a pending PR, finish that review and return to updated `main` before continuing or reconciling.

Run PowerShell 7 from the workspace root on the VNet-connected management host. You need AKS Azure RBAC Cluster Admin for platform onboarding, Contributor on AKS, Resource Policy Contributor for assignments/exemptions, and permission to assign Azure roles. Team users do not need these privileges.

**Task:** record the operator context, application reconciler, source and Git revision, and establish which identity owns platform-level changes. Do not expand the app reconciler's namespace-scoped permissions for this lab.

<details>
<summary>Solution</summary>

```powershell
. .\scripts\Use-Lab.ps1
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
New-Item .\.artifacts\advanced -ItemType Directory -Force | Out-Null
$Out = az deployment group show -g $Lab.ResourceGroup -n foundation --query properties.outputs -o json | ConvertFrom-Json
$ClusterId = $Out.clusterId.value
kubectl config current-context
flux get kustomizations -A
flux get sources git -A
```

Record the current context and Git revision. Identify the existing application's Flux Kustomization and GitRepository from lab 4:

```powershell
# Set these to the names printed above, NOT to a guessed cluster name.
$AppKustomization = 'orders'
$GitSource = 'flux-system'
$FluxNamespace = 'flux-system'
```

If lab 4 used different names, substitute them in every later lab. `$GitSource` must be the source whose checkout contains `advanced`. Keep platform scope separate: the app reconciler must not acquire permission to create namespaces or ClusterRoles. The following `teams` and `storage` platform Kustomizations use the **existing trusted platform Flux controller**, not the application's restricted service account. A platform repository is preferable in production.

</details>

## 2. Review and reconcile the team guardrails

1. Read `advanced\governance\teams.yaml`. It includes two namespaces, restricted PSA, zero load-balancer quota, bounded compute/storage, LimitRanges, same-namespace traffic, and DNS-only external egress.
2. Pin the three PSA version labels to the current Kubernetes **minor** for predictable policy changes. Upgrade these labels deliberately in lab 9; leaving the version unspecified tracks the API server's policy version.
3. Publish this folder to the existing lab-4 Git remote. Do not create a new repository.

Persist the platform-owned reconciliation in the existing bootstrap path, and prove the team guardrails are Ready at the reviewed Git SHA. Do not put namespace creation under the application service account.

<details>
<summary>Solution</summary>

```powershell
$Version = az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query kubernetesVersion -o tsv
$PsaVersion = 'v' + (($Version -split '\.')[0..1] -join '.')
$Teams = Get-Content .\advanced\governance\teams.yaml -Raw
$Teams = $Teams -replace '(?m)^ +pod-security[.]kubernetes[.]io/(enforce|audit|warn)-version:.*\r?\n', ''
foreach ($Mode in 'enforce','audit','warn') {
  $Teams = $Teams.Replace("    pod-security.kubernetes.io/${Mode}: restricted",
    "    pod-security.kubernetes.io/${Mode}: restricted`n    pod-security.kubernetes.io/${Mode}-version: $PsaVersion")
}
$Teams | Set-Content .\advanced\governance\teams.yaml
kubectl kustomize .\advanced\governance
git add .\advanced\governance
.\advanced\Publish-ReviewedChange.ps1 -Message "Onboard isolated internal teams"
flux create kustomization teams --namespace $FluxNamespace --source "GitRepository/$GitSource" --path ./advanced/governance --prune --interval 1m
flux reconcile kustomization teams --namespace $FluxNamespace --with-source
kubectl get ns team-a team-b --show-labels
kubectl get quota,limitrange,networkpolicy -n team-a
```

Persist the `teams` Flux CR in the platform bootstrap path used in lab 4 so it is reconstructable. The `flux create` command is a **bootstrap control-plane action**, not an alternative source of workload configuration.

The primary platform root has an explicit resource list; copying a file without registering it does not reconcile it. Export and register this child without changing `orders`' namespace-scoped service account:

```powershell
flux export kustomization teams -n $FluxNamespace | Set-Content .\gitops\clusters\primary\teams.yaml
$PlatformRoot = '.\gitops\clusters\primary\kustomization.yaml'
$PlatformText = Get-Content $PlatformRoot -Raw
if ($PlatformText -notmatch '(?m)^  - teams[.]yaml\s*$') {
  $PlatformText = $PlatformText.Replace('resources:', "resources:`n  - teams.yaml")
  $PlatformText | Set-Content $PlatformRoot
}
git add .\gitops\clusters\primary\teams.yaml .\gitops\clusters\primary\kustomization.yaml
.\advanced\Publish-ReviewedChange.ps1 -Message "Persist platform-owned team reconciliation"
flux reconcile kustomization flux-system -n $FluxNamespace --with-source
```

Expected: `teams` Ready at the pushed SHA; no externally exposed service; explicit resource limits. DNS allowance uses the kube-dns labels; verify those labels with `kubectl get pods -n kube-system -l k8s-app=kube-dns --show-labels`. NetworkPolicy is additive: inspect all policies before asserting isolation.

The supplied budgets allow 10 pods, aggregate CPU requests of 2 and CPU limits of 4, memory requests of 4 GiB and limits of 8 GiB, and 20 GiB of requested storage per namespace. No LoadBalancer Service is allowed. LimitRange defaults are 100m/64Mi requests and 500m/128Mi limits, with a per-container maximum of 1 CPU and 1Gi memory. Same-namespace pod traffic and kube-dns on TCP/UDP 53 are allowed; other external egress is not. These are separate capacity, admission and network boundaries, not one interchangeable control.

</details>

## 3. Delegate access and prove a cross-team denial

Use existing Entra group object IDs for real customer access; do not create users or give Owner to application teams.

**Task:** give team A namespace-scoped delivery access and prove allowed and denied operations as a real team member. Separately exercise a synthetic service account; do not present administrator or impersonated service-account results as proof of human Entra authorization.

<details>
<summary>Solution</summary>

```powershell
$TeamAGroupObjectId = Read-Host 'Existing team A Entra group object ID'
az role assignment create --assignee-object-id $TeamAGroupObjectId --assignee-principal-type Group `
  --role 'Azure Kubernetes Service Cluster User Role' --scope $ClusterId
az role assignment create --assignee-object-id $TeamAGroupObjectId --assignee-principal-type Group `
  --role 'Azure Kubernetes Service RBAC Writer' --scope "$ClusterId/namespaces/team-a"
```

An actual team-A member signs in on a **separate authorized management session**, gets non-admin AKS credentials and runs:

```powershell
. .\scripts\Use-Lab.ps1
az login --tenant $Lab.TenantId
az aks get-credentials -g $Lab.ResourceGroup -n $Lab.ClusterName --overwrite-existing
kubelogin convert-kubeconfig -l azurecli
kubectl auth can-i create deployments -n team-a
```

Run each expected-denial command separately, since native failures stop the current invocation:

```powershell
kubectl auth can-i get secrets -n team-b
```

```powershell
kubectl auth can-i create clusterrolebindings
```

Expected: yes/no/no after RBAC propagation. A platform admin's own `can-i` output does **not** prove team authorization. RBAC Writer includes powerful namespace operations including access to secrets and running pods as namespace service accounts; namespace workload identities must therefore have only that team's permissions.

Cluster User permits retrieval of the non-admin kubeconfig; it does not grant Kubernetes data-plane administration. If the member already inherits broader access, these role assignments do not remove it: use a member without broader roles for the denial exercise and document inherited access.

For a repeatable test without another person, create a synthetic Kubernetes service account with a deliberately narrow native Role. These **incident test objects** are not Flux-owned:

```powershell
kubectl create serviceaccount access-probe -n team-a
kubectl create role pod-observer -n team-a --verb=get,list --resource=pods
kubectl create rolebinding pod-observer -n team-a --role=pod-observer --serviceaccount=team-a:access-probe
kubectl auth can-i list pods -n team-a --as=system:serviceaccount:team-a:access-probe
# Expected denial returns a nonzero exit; temporarily allow it for this one test.
$PSNativeCommandUseErrorActionPreference = $false
kubectl auth can-i list pods -n team-b --as=system:serviceaccount:team-a:access-probe
$PSNativeCommandUseErrorActionPreference = $true
```

This tests service-account authorization, **not** the human Entra path. Impersonation requires the platform administrator's permissions.

</details>

## 4. Attribute PSA and capacity failures correctly

The test pod is outside `advanced\governance\kustomization.yaml` intentionally. It is an ephemeral probe, not a managed workload.

**Task:** prove distinct PSA, per-container capacity and aggregate-quota denials, then remove the disposable quota pods before the next task. Keep the guardrails enforced; do not relabel the namespace privileged or remove quotas to make a probe succeed.

<details>
<summary>Solution</summary>

```powershell
kubectl apply -f .\advanced\governance\test-pod.yaml
kubectl wait -n team-a pod/team-probe --for=condition=Ready --timeout=180s
$Pod = kubectl get pod team-probe -n team-a -o json | ConvertFrom-Json
$Bad = Get-Content .\advanced\governance\test-pod.yaml -Raw
$Bad = $Bad.Replace('name: team-probe','name: privileged-probe').Replace('allowPrivilegeEscalation: false','allowPrivilegeEscalation: true')
$Bad | Set-Content .\.artifacts\advanced\privileged.yaml
$PSNativeCommandUseErrorActionPreference = $false
kubectl apply --dry-run=server -f .\.artifacts\advanced\privileged.yaml
$PSNativeCommandUseErrorActionPreference = $true
```

Expected denial explicitly names **PodSecurity restricted**. No privileged pod is created. Solution: retain `allowPrivilegeEscalation: false`, `runAsNonRoot`, RuntimeDefault seccomp and `capabilities.drop: ALL`; do not relabel the namespace `privileged`.

Now ask for resources beyond the per-container cap:

```powershell
$TooLarge = (Get-Content .\advanced\governance\test-pod.yaml -Raw).Replace('name: team-probe','name: oversize-probe').Replace('cpu: 200m','cpu: "2"')
$TooLarge | Set-Content .\.artifacts\advanced\oversize.yaml
$PSNativeCommandUseErrorActionPreference = $false
kubectl apply --dry-run=server -f .\.artifacts\advanced\oversize.yaml
$PSNativeCommandUseErrorActionPreference = $true
kubectl describe limitrange defaults -n team-a
kubectl describe quota team-budget -n team-a
```

Expected **LimitRange** denial, not "the scheduler is broken." To exercise the aggregate pod quota, create individually valid, uniquely named copies until the eleventh pod is denied (including the existing probe):

```powershell
$PSNativeCommandUseErrorActionPreference = $false
1..10 | ForEach-Object {
  (Get-Content .\advanced\governance\test-pod.yaml -Raw).Replace('name: team-probe',"name: quota-$_") | kubectl apply -f -
}
$PSNativeCommandUseErrorActionPreference = $true
kubectl describe quota team-budget -n team-a
```

Expected `exceeded quota` once the pod count reaches 10. Approved requests still work. Repair is deleting the disposable copies or negotiating a capacity-backed quota increase through Git, not removing quotas.

Delete the quota copies **before the policy test**, otherwise the namespace's full quota would hide the intended audit/admission result:

```powershell
1..10 | ForEach-Object { kubectl delete pod -n team-a "quota-$_" --ignore-not-found }
kubectl wait -n team-a --for=delete pod/quota-1 --timeout=120s
```

</details>

## 5. Move Azure Policy from audit to deny

Use one built-in image-source policy so its denial is distinguishable from PSA. Policy installs Gatekeeper; **do not install a second Gatekeeper or hand-manage its generated constraints**.

**Task:** discover the live built-in allowed-image policy, scope it to the two team namespaces, demonstrate Audit admission and noncompliance, then demonstrate Deny enforcement. Allow asynchronous sync; an image-pull failure is not policy-denial evidence.

<details>
<summary>Solution</summary>

```powershell
az aks enable-addons -g $Lab.ResourceGroup -n $Lab.ClusterName --addons azure-policy
$Definitions = az policy definition list -o json | ConvertFrom-Json
$Definition = @($Definitions | Where-Object {
  $display = $_.PSObject.Properties['displayName']
  $properties = $_.PSObject.Properties['properties']
  if ($display) { $display.Value -eq 'Kubernetes cluster containers should only use allowed images' }
  elseif ($properties) { $properties.Value.displayName -eq 'Kubernetes cluster containers should only use allowed images' }
  else { $false }
})
if ($Definition.Count -ne 1) { throw 'Inspect Kubernetes built-ins; the expected policy definition was not uniquely found.' }
az policy definition show --name $Definition[0].name -o json | Set-Content .\.artifacts\advanced\image-policy-definition.json
Get-Content .\.artifacts\advanced\image-policy-definition.json
```

Confirm the live definition has `effect`, `namespaces`, and `allowedContainerImagesRegex` before assigning:

```powershell
$PolicyParameters = @{
  effect = @{value='audit'}
  namespaces = @{value=@('team-a','team-b')}
  allowedContainerImagesRegex = @{value='^mcr[.]microsoft[.]com/.+$'}
}
$PolicyParameters | ConvertTo-Json -Depth 8 | Set-Content .\.artifacts\advanced\image-policy-parameters.json
az policy assignment create -n team-image-sources --scope $ClusterId --policy $Definition[0].id `
  --params '@.\.artifacts\advanced\image-policy-parameters.json'
```

Wait for the add-on and constraint sync (often 15–30 minutes), then save `kubectl get constraints -o yaml` and the Azure Policy compliance record. Test an otherwise PSA-compliant pod using an unapproved registry:

```powershell
$Unapproved = (Get-Content .\advanced\governance\test-pod.yaml -Raw).Replace('name: team-probe','name: image-probe').Replace('mcr.microsoft.com/azurelinux/base/core:3.0','docker.io/library/busybox:1.37')
$Unapproved | Set-Content .\.artifacts\advanced\image-probe.yaml
kubectl apply -f .\.artifacts\advanced\image-probe.yaml
```

Audit admits it; an image-pull firewall failure is unrelated. Wait for a noncompliant record before changing to Deny. Delete the probe, modify `effect.value` to `Deny`, and reapply the **same** assignment:

```powershell
kubectl delete pod image-probe -n team-a --ignore-not-found
$PolicyParameters.effect.value = 'deny'
$PolicyParameters | ConvertTo-Json -Depth 8 | Set-Content .\.artifacts\advanced\image-policy-parameters.json
az policy assignment create -n team-image-sources --scope $ClusterId --policy $Definition[0].id `
  --params '@.\.artifacts\advanced\image-policy-parameters.json'
# Wait for Gatekeeper enforcement sync; then:
$PSNativeCommandUseErrorActionPreference = $false
kubectl apply --dry-run=server -f .\.artifacts\advanced\image-probe.yaml
$PSNativeCommandUseErrorActionPreference = $true
```

Expected denial names an Azure Policy/Gatekeeper constraint. Keep the constraint and assignment ID in evidence. Built-in compliance scans are asynchronous; a just-created assignment is not proof of enforcement. Production would allow your ACR by anchored registry regex; this lab policy is scoped to the two test namespaces, not `orders`.

</details>

## 6. Record exceptions and Defender coverage

An exception needs an owner, ticket, compensating control, exact scope and expiry. Kubernetes namespaces are not ARM scopes for Azure Policy exemptions. Do not fabricate `.../namespaces/team-a` as an exemption ARM resource. For a namespace-specific image exception, use a separately owned policy assignment with a narrower `namespaces` parameter and a **Git-controlled scheduled removal**. A cluster-scoped exemption is broader and requires explicit approval:

**Task:** document and remove a time-bounded diagnostic exception, then record Defender's before-state, enabled coverage and a digest-specific assessment or an explicit coverage blocker. Obtain subscription-owner approval before changing billable Defender pricing. Do not deploy a deliberately vulnerable image.

<details>
<summary>Solution</summary>

```powershell
$Assignment = az policy assignment show -n team-image-sources --scope $ClusterId --query id -o tsv
# Demonstrate on this dedicated lab cluster only; remove immediately after inspection.
$Expiry = [DateTime]::UtcNow.AddHours(1).ToString('yyyy-MM-ddTHH:mm:ssZ')
az policy exemption create -n lab-image-waiver --scope $ClusterId --policy-assignment $Assignment `
  --exemption-category Waiver --expires-on $Expiry --description 'LAB-007; platform owner; one-hour diagnostic waiver'
az policy exemption show -n lab-image-waiver --scope $ClusterId
az policy exemption delete -n lab-image-waiver --scope $ClusterId
```

For the example record, use ticket `LAB-007`, the named platform operator as owner, this dedicated cluster's exact ARM ID as scope, and the generated one-hour UTC expiry. State the compensating control: no customer workloads, only the diagnostic probe under operator control, with PSA and network/quota controls retained. Attach approval and the exemption's deletion evidence. For a real namespace exception, overlapping Deny assignments still apply: exclude only the approved namespace from the original assignment and place it under a separate narrowly adjusted assignment in the same reviewed change, then restore the original coverage at expiry.

Enable Defender only after recording subscription state:

```powershell
az security pricing show -n Containers -o json | Set-Content .\.artifacts\advanced\defender-before.json
az security pricing create -n Containers --tier Standard `
  --extensions name=ContainerRegistriesVulnerabilityAssessments isEnabled=True `
  --extensions name=AgentlessDiscoveryForKubernetes isEnabled=True
az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --enable-defender `
  --defender-config "logAnalyticsWorkspaceResourceId=$($Out.workspaceId.value)"
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query securityProfile.defender
kubectl get pods -A | Select-String 'defender|security'
```

In Defender for Cloud → Environment settings → subscription → Containers, verify **registry access** and **agentless vulnerability assessment** coverage for the ACR, and review recommendations after a freshly built lab image has been pushed. Private registry scanning support and coverage must be checked in the linked support matrix; a sensor Running does not prove registry scan coverage. Save the assessment's registry, digest, timestamp, CVEs and remediation owner. Allow scan latency. Do not deploy a deliberately vulnerable image just to produce an alert. Defender detection/scanning does **not automatically reject images at admission**; the allowed-registry policy is source enforcement, not vulnerability enforcement.

</details>

## 7. Debrief, evidence and cumulative cleanup

Deliver: Git SHA, PSA version, real team access results, service-account results labeled separately, quota/LimitRange errors, Audit then Deny evidence, exception expiry, Defender pricing/coverage and one digest-specific assessment or an explicit pending-coverage blocker.

**Task:** answer the customer questions below and remove only incident probes. Retain team guardrails and Defender for the cumulative labs; subscription-wide pricing changes at final teardown require the owner's review.

<details>
<summary>Model answer: Can we host competitors in two namespaces?</summary>

Not on namespace controls alone. Nodes/kernel, control plane, cluster add-ons and privileged operators are shared. Choose separate clusters/subscriptions where trust, regulation or blast-radius requirements demand it.

</details>

<details>
<summary>Model answer: Why PSA and Azure Policy?</summary>

PSA supplies standardized pod privilege boundaries; Policy adds centrally reported, assignable organization-specific controls. Avoid duplicate denials without clear ownership.

</details>

<details>
<summary>Model answer: Do network policies encrypt traffic?</summary>

No. They control allowed flows. A mesh can supply workload mTLS and identity-based L7 policy, but adds certificates, proxies, upgrades, latency and debugging cost; do not buy it just to obtain default deny.

</details>

<details>
<summary>Model answer: Does Defender make a compliant image safe?</summary>

No. Scan evidence ages, runtime threats differ, and risk acceptance and patch SLAs remain operational responsibilities.

</details>

<details>
<summary>Solution: cumulative cleanup</summary>

Delete only probes now; retain team controls and Defender for later labs:

```powershell
kubectl delete pod -n team-a team-probe image-probe --ignore-not-found
1..10 | ForEach-Object { kubectl delete pod -n team-a "quota-$_" --ignore-not-found }
kubectl delete rolebinding pod-observer -n team-a
kubectl delete role pod-observer -n team-a
kubectl delete serviceaccount access-probe -n team-a
```

The evidence should attribute each denial to its own layer: the human/service-account identity and target scope for RBAC, the restricted PSA rule, the LimitRange maximum, the aggregate quota, and the Azure Policy constraint. Record pending Defender coverage as incomplete, not as a successful scan.

</details>

At **final pack teardown**, remove `teams` from platform Git first and reconcile pruning; remove the lab-created team Cluster User and namespace Writer role assignments and `team-image-sources` assignment. Do not delete the namespaces while they contain customer data. Revert Defender pricing only if this exercise changed it and no other cluster depends on it; restoring the whole prior subscription pricing/extension configuration requires the subscription owner's review.

## Official references and status

Checked **2026-09-10**: [AKS Azure Policy](https://learn.microsoft.com/azure/aks/use-azure-policy), [built-in policies](https://learn.microsoft.com/azure/aks/policy-reference), [PSA](https://learn.microsoft.com/azure/aks/use-psa), [Azure RBAC](https://learn.microsoft.com/azure/aks/manage-azure-rbac), [Defender support](https://learn.microsoft.com/azure/defender-for-cloud/support-matrix-defender-for-containers). Required paths use GA features. The installed CLI and live policy definition are the source of truth for parameter availability; record versions in evidence.
