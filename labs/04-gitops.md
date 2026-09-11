# Lab 4 — Deliver releases through GitOps and a private supply chain

**Scenario.** A platform team needs auditable releases without giving an image-build pipeline Kubernetes access. Build once, promote an immutable digest, reconcile desired state, and recover through Git.

**Prerequisites/re-entry.** Complete labs 1–3. Use PowerShell 7 at the repository root on the VNet-connected management host. `local.settings.json`, `foundation`, private DNS, HTTPS application access and working Workload ID must exist. Install the supported Flux CLI, GitHub CLI, Git, Azure CLI, kubectl and kubelogin on that host; a **separate operator-provisioned Linux runner** needs Docker Engine/buildx, PowerShell 7 and Azure CLI. Docker commands below run on a host with a Linux Docker engine and private ACR connectivity; they need not run on the Windows management VM. An existing private GitHub repository, permission to configure its environments/runners, and a protected `main` branch are required. No command here creates a GitHub repository.

**Ownership/path.** CI → Entra OIDC → ACR private endpoint writes images only. Flux → HTTPS Git with a repository credential pulls manifests; kubelet → ACR with its existing pull identity fetches images. GitHub OIDC is **not** authentication from Flux to a private Git repository. Platform bootstrap reconciles `gitops\clusters\primary`; the **`orders` Kustomization in `flux-system`** reconciles `gitops\clusters\primary\apps\orders`. `orders-test` uses its own namespace, queue and two managed identities. It is still the same cluster and Service Bus namespace, not a production isolation boundary.

**Inventory/permissions/cost.** Reuse AKS, ACR Premium, Service Bus Premium, Firewall and private management connectivity. Add three user-assigned managed identities, three federations and one queue; identities have no hourly compute charge, while runner VM/storage, image storage, registry transfers and extra test replicas do. A human temporarily needs the AKS **Azure Kubernetes Service RBAC Cluster Admin** role for Flux CRDs/bootstrap, not `--admin` credentials. Its standing application role remains namespace scoped. The infrastructure operator needs resource deployment and role-assignment permissions. CI receives only **AcrPush on this registry** (classic RBAC registry mode); it receives no AKS, subscription Contributor, Key Vault or Service Bus role. With ABAC-enabled ACR, use repository-scoped repository writer permissions instead of assuming AcrPush works.

## Numbered directives

### 1. Establish the context and verify private paths

**If this curriculum folder has no `.git`, establish the working checkout before running the block below.** Clone the already-created private repository into an approved directory, then copy the curriculum source files there, including `.gitignore` and `.github`, but excluding `.venv`, `.artifacts` and any `.git` directory from another checkout. Separately carry `local.settings.json` and the **accumulated** `rendered` directory to the new checkout as ignored local state: later steps need the resolved base, CSI patch, policies, private gateway configuration and certificate files. Do not add any of that local state to Git. Switch PowerShell to the new checkout and inspect `git status --short --ignored` before the first commit. Do not regenerate `rendered\base`, and do not leave the settings behind. If already working in the intended repository, no move is needed.

```powershell
. .\scripts\Use-Lab.ps1
$GitHubRepository = Read-Host 'Existing private GitHub repository (owner/name)'
if ($GitHubRepository -notmatch '^[^/]+/[^/]+$') { throw 'Use owner/name' }
gh repo view $GitHubRepository --json nameWithOwner,isPrivate,defaultBranchRef
git remote -v
git status --short
az aks get-credentials -g $Lab.ResourceGroup -n $Lab.ClusterName --overwrite-existing
kubelogin convert-kubeconfig -l azurecli
kubectl auth can-i get deployments -n orders
Resolve-DnsName $Lab.RegistryServer
Test-NetConnection $Lab.RegistryServer -Port 443
flux version --client
flux check --pre
```

**Evidence:** the existing remote is the chosen repository, the API uses Entra auth, ACR resolves to its private endpoint, and Flux preflight succeeds. Private settings/rendered files remain ignored. Do not initialize/push to an accidental new remote.

### 2. Provision the publisher identity and a real test environment

```powershell
az deployment group create -g $Lab.ResourceGroup -n delivery `
  --template-file .\ops\delivery.bicep `
  --parameters prefix=$Lab.Prefix location=$Lab.Location acrName=$Lab.AcrName `
    githubRepository=$GitHubRepository serviceBusName=$Lab.ServiceBusName `
    oidcIssuer=$Outputs.oidcIssuer.value
$Delivery = az deployment group show -g $Lab.ResourceGroup -n delivery --query properties.outputs -o json | ConvertFrom-Json
az role assignment list --scope $Outputs.acrId.value --all -o table
```

In the existing repository's **Settings → Environments**, create `acr-publish`, require reviewers and restrict deployments to `main`. Register an **ephemeral self-hosted Linux x64 runner** with labels `self-hosted,Linux,X64,aks-private`, restricted to this repository. Use a current supported runner **at least 2.329.0** for the pinned Node.js 24 actions. Its VNet/DNS must reach the ACR login **and regional data endpoints**. Allow outbound GitHub Actions endpoints, Entra, ARM, the Docker base registry, package indexes and SBOM generator sources through the runner's approved firewall path. Use the GitHub registration instructions for the current runner version; registration tokens are short-lived secrets, not files to commit. This artifact workflow targets GitHub.com; GitHub Enterprise Server requires its own supported artifact-action matrix.

```powershell
gh variable set AZURE_CLIENT_ID --env acr-publish --repo $GitHubRepository --body $Delivery.publisherClientId.value
gh variable set AZURE_TENANT_ID --env acr-publish --repo $GitHubRepository --body $Lab.TenantId
gh variable set AZURE_SUBSCRIPTION_ID --env acr-publish --repo $GitHubRepository --body $Lab.SubscriptionId
gh variable set ACR_NAME --env acr-publish --repo $GitHubRepository --body $Lab.AcrName
gh variable set REGISTRY_SERVER --env acr-publish --repo $GitHubRepository --body $Lab.RegistryServer
```

**Evidence:** federated subject is exactly `repo:OWNER/REPO:environment:acr-publish`; the workflow's `environment` matches. No client secret exists. Never run untrusted pull requests on this runner; repository write access is a powerful supply-chain privilege. A normal GitHub-hosted runner cannot reach this private registry. A hosted alternative requires an explicitly configured supported private-networking service and equivalent verification; it is not this lab's default.

### 3. Publish once, record its digest, and mirror platform controller images

Review `.github\workflows\publish-image.yml` and commit the authored curriculum to the existing protected repository through its normal review process. The workflow runs on `main` changes under `app` or manual dispatch, **not** pull requests. The image build is `app\Dockerfile`, context `app`, linux/amd64. SBOM and provenance attestations are evidence, not vulnerability admission enforcement.

```powershell
gh workflow run publish-image.yml --repo $GitHubRepository --ref main
gh run list --repo $GitHubRepository --workflow publish-image.yml --limit 5
$RunId = Read-Host 'Successful reviewed workflow run ID'
gh run view $RunId --repo $GitHubRepository
gh run download $RunId --repo $GitHubRepository --pattern 'order-image-*' --dir .artifacts\image
$Reference = (Get-ChildItem .artifacts\image -Recurse -Filter image-reference.txt |
    Select-Object -First 1 | Get-Content).Trim()
$Digest = ($Reference -split '@')[1]
if ($Digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'No valid image digest' }
$Reference
```

On the VNet-connected Docker host, using a separately authorized operator's **registry-scoped push permission**, mirror Flux's matching CLI-version controller images into ACR. This avoids node pulls from `ghcr.io` after egress closure:

```powershell
# Same Flux CLI version must be used here and in directive 5.
az acr login --name $Lab.AcrName
.\ops\Mirror-FluxImages.ps1 -RegistryServer $Lab.RegistryServer
```

**Evidence:** the workflow summary/artifact gives `REGISTRY/order-app@sha256:…`, push succeeds against the private endpoint, and the four Flux controller images exist under `fluxcd/`. Do not use `az acr build` on the default public task pool after registry public access is disabled. The workflow pins Actions to upstream release commit SHAs verified on the review date; update those pins through reviewed dependency changes. Also restrict allowed actions, add vulnerability/license checks, and enforce signed provenance at admission; this lab does not falsely equate SBOM creation with trust.

### 4. Transfer application ownership to Git, not live YAML patches

```powershell
# Preserve the accumulated lab 2 identity and lab 3 policy additions.
Get-ChildItem .\rendered\base
kubectl kustomize .\rendered\base
.\ops\Initialize-GitOps.ps1
.\ops\Set-GitOpsImage.ps1 -Namespace orders-test -Digest $Digest
kubectl kustomize .\gitops\clusters\primary\apps\orders-test
git diff --stat
git status --short
git switch -c adopt-orders-gitops
git add gitops
git commit -m "Initialize namespace-scoped GitOps application sources"
git push -u origin HEAD
gh pr create --repo $GitHubRepository --base main --fill
# After review/merge:
git switch main
git pull --ff-only
```

**Evidence:** `orders-test` has test identity client IDs and `QUEUE_NAME=orders-test`; `orders` keeps its original identities/queue. The initialization script is deliberately one-shot. It copies application/network sources, not runtime secrets or local settings. Namespaces and reconciler roles belong to the platform root; application reconcilers cannot write namespaces/CRDs or cluster roles.

The **accumulated** rendered base is the adoption contract. **Do not run `Render-Manifests.ps1` again here**: that would lose Lab 2's identity and Lab 3's policy additions. Initialization copies the complete accumulated directory, including `secret-provider.yaml`, `identity-patch.yaml`, `policies.yaml` and their Kustomize entries. It then removes only the Namespace resource reference from each app Kustomization because Namespace reconciliation belongs to the platform root (the copied Namespace file remains an unreferenced snapshot).

The primary retains the CSI mount; its reconciler Role permits `secretproviderclasses`, not Kubernetes Secret access. `/config-version` continues rereading the synthetic `/mnt/secrets-store/lab-version`; verify it after adoption. The test copy deliberately removes the CSI resource/patch references and files: test identities have no Key Vault role. Lab 2 must already have created `lab-version` and enabled the CSI provider; repair those prerequisites instead of opening Key Vault access.

Gateway/TLS remain **manually managed by the Lab 3 platform operator**, outside application Flux: `rendered\gateway.yaml` contains Namespace `gateway-system`, Gateway `gateway-system/orders-gateway` with GatewayClass `approuting-istio`, and HTTPRoute `orders` in namespace `orders`. The copied policy permits `gateway-system` to reach API port 8080. Do not copy that cross-namespace manifest into an app Kustomization or apply a namespace override to it; do not copy locally generated TLS private keys into Git. Preserve the platform manifest/inputs and its explicit owner for later gateway updates.

### 5. Bootstrap against the existing repository and reduce runtime Git permission

Use a short-lived fine-grained GitHub PAT for **this existing repository only**, with Contents read/write, Metadata read, Administration read as required by Flux bootstrap; ensure organization approval/SSO. Do not record a transcript while entering credentials. `Read-Host -MaskInput` avoids shell history; environment values and Kubernetes Secrets are still sensitive. The bootstrap needs a temporary branch-policy exception for its generated controller commit, or an approved platform process; remove the exception immediately.

```powershell
$parts = $GitHubRepository.Split('/')
$env:GITHUB_TOKEN = Read-Host 'Short-lived bootstrap PAT (masked)' -MaskInput
try {
    # Existing repository was verified in directive 1; bootstrap must not create another.
    flux bootstrap github --owner=$($parts[0]) --repository=$($parts[1]) `
      --branch=main --path=gitops/clusters/primary --token-auth `
      --registry="$($Lab.RegistryServer)/fluxcd"
} finally {
    Remove-Item Env:\GITHUB_TOKEN -ErrorAction SilentlyContinue
}
git pull --ff-only
$ReadOnlyPat = Read-Host 'Separate runtime PAT: this repository, Contents read only (masked)' -MaskInput
try {
    flux create secret git flux-system --namespace=flux-system `
      --url="https://github.com/$GitHubRepository" --username=git --password=$ReadOnlyPat
} finally {
    $ReadOnlyPat = $null
}
flux reconcile source git flux-system
flux reconcile kustomization flux-system --with-source
flux reconcile kustomization orders-test --with-source
flux get kustomizations
kubectl -n orders-test rollout status deployment/order-api --timeout=300s
kubectl -n orders-test rollout status deployment/order-worker --timeout=300s
```

For a **personal-account repository**, add `--personal` to bootstrap. Do not revoke the read-only runtime PAT. Schedule its rotation and alert on `GitRepository Ready=False`; replace the Secret using the same command. A read-only SSH deploy key is another supported choice, but requires approved SSH egress, known-host verification, bootstrap write access and separate key rotation—not GitHub Actions OIDC.

**Evidence:** `flux-system`, `orders`, `orders-test` are Ready; `orders` records an applied Git revision. Flux's root platform reconciler is privileged; protecting its Git path is essential. `serviceAccountName: orders-reconciler` constrains the two child reconcilers. After bootstrap withdraw the human's temporary cluster-wide role through your existing access/PIM process. Later privileged lab directives must be run by the designated platform operator, not by widening CI's identity.

### 6. Validate in test and promote the exact same digest by pull request

In another PowerShell terminal:

```powershell
kubectl -n orders-test port-forward service/order-api 8082:80
```

In the original terminal:

```powershell
.\ops\Invoke-OrderLoad.ps1 -BaseUri http://127.0.0.1:8082 -Count 10 -Concurrency 1 -OutputPath .artifacts\test-release.json
kubectl -n orders-test logs deployment/order-worker --tail=30
git switch -c promote-orders-digest
.\ops\Set-GitOpsImage.ps1 -Namespace orders -Digest $Digest
git add gitops
git commit -m "Promote verified orders image digest"
git push -u origin HEAD
gh pr create --repo $GitHubRepository --base main --fill
```

Have the designated reviewer approve and merge the PR using the organization's normal workflow. Stop the test port-forward with Ctrl+C. After merge:

```powershell
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
kubectl -n orders rollout status deployment/order-api --timeout=300s
kubectl -n orders get deployments -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}'
kubectl -n orders exec deployment/order-api -c api -- python -c `
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/config-version').read().decode())"
```

**Evidence:** test requests return 202, test worker logs processed synthetic IDs, both primary deployments use the same verified digest, and primary `/config-version` still returns the current synthetic Key Vault value from Lab 2. Before Lab 8, GET order lookup returns 503 by design; a 202 only proves enqueue acceptance, not durable business completion.

### 7. Prove drift correction, then introduce and recover a bad release

Before Lab 6 adds autoscalers, manually scaling is a harmless drift test:

```powershell
kubectl -n orders scale deployment/order-api --replicas=3
flux reconcile kustomization orders
kubectl -n orders get deployment order-api
```

**Expected:** returns to the Git replica count (normally two). After HPA owns replicas in Lab 6, do **not** use replicas to test Flux drift.

Make an intentionally nonexistent digest only in **test**, through a reviewed change:

```powershell
git switch -c inject-bad-test-release
.\ops\Set-GitOpsImage.ps1 -Namespace orders-test -Digest ('sha256:' + ('0' * 64))
git add gitops
git commit -m "Exercise a deliberately missing test image"
git push -u origin HEAD
gh pr create --repo $GitHubRepository --base main --fill
```

After approving/merging this explicitly labeled fault:

```powershell
git switch main
git pull --ff-only
$BadCommit = git rev-parse HEAD
flux reconcile kustomization orders-test --with-source
# Expected nonzero reconciliation timeout: inspect in a new command after it fails.
kubectl -n orders-test get pods
kubectl -n orders-test get events --sort-by=.lastTimestamp
flux get kustomizations
```

**Troubleshooting injection:** new pods show `ErrImagePull`/`ImagePullBackOff`; old Ready pods may still serve, so a working endpoint alone is not rollout success. Distinguish `manifest unknown` from DNS/TLS/403 registry failures. A `Forbidden` reconciliation error points to the reconciler Role rather than ACR.

**Solution:** restore the verified digest through a new PR (works for merge, rebase or squash histories; no assumption about merge-commit parentage):

```powershell
git switch -c repair-test-release
.\ops\Set-GitOpsImage.ps1 -Namespace orders-test -Digest $Digest
git add gitops
git commit -m "Restore verified test image after controlled failure"
git push -u origin HEAD
gh pr create --repo $GitHubRepository --base main --fill
# After approval/merge:
git switch main
git pull --ff-only
flux reconcile kustomization orders-test --with-source
kubectl -n orders-test rollout status deployment/order-api --timeout=300s
```

Record both Git revisions and Ready conditions. For a real incident, suspend only the affected child Kustomization with `flux suspend kustomization orders`; log the emergency change, repair Git, then resume and reconcile. Never leave reconciliation suspended as the final state.

### 8. Capture handover evidence and control costs

```powershell
flux get all --all-namespaces
kubectl -n orders get deployments,services,pdb
kubectl -n orders-test get deployments,services
az acr repository show-tags --name $Lab.AcrName --repository order-app -o table
```

Keep the primary GitOps controller/workloads for labs 5–10. Stop ephemeral runner capacity after builds, preserving its registration/security process rather than leaving a privileged shared machine idle. Test resources may be retained for later promotion; their replicas consume real capacity.

For **final teardown only**, remove child Kustomizations from the platform Git root and merge; wait for pruning, then uninstall Flux with `flux uninstall --namespace=flux-system` while the private API is reachable. Revoke the runtime Git credential, delete runner registrations/environment federation, and follow root resource-group cleanup. **Suspending Flux does not stop workloads or remove charges.** Do not prune the `orders` namespace mid-course; later labs depend on its identities, policies and data.

## Customer discovery and model answers

| Question | Model answer |
|---|---|
| Why pull delivery rather than a CI kubeconfig? | CI proves/builds an artifact; an in-cluster controller reconciles it. This reduces pipeline access to the private API, but the Git root and Flux service accounts remain privileged assets. |
| Is GitHub OIDC completely secretless delivery? | It removes the CI-to-Azure client secret. Private Git authentication, registry pull identity, TLS keys and application secrets have separate lifecycles. |
| Can an untrusted PR use our private runner safely? | Not by default. It could steal reachable credentials or pivot into the VNet. Isolate ephemeral runners, avoid PR triggers and require protected environment approvals. |
| Is `orders-test` production-like isolation? | It exercises separate desired state, queue and identities, but shares nodes, cluster administrators and a Service Bus namespace. Stronger trust/compliance boundaries justify separate clusters/subscriptions. |
| Can someone overwrite the image tag? | Tags are mutable unless policy prevents it; workloads use a digest. Protect ACR write access and retention so the referenced digest remains available. |
| What is a safe hotfix? | Declare incident ownership; suspend the smallest reconciliation scope, make an audited temporary fix, commit the durable correction, resume and verify. |

## References and support

Source review: **2026-09-10**. Required path: supported Flux v2 APIs, stable Kubernetes APIs, Entra federation and GA private ACR. Flux is upstream self-managed here, **not** the Azure Flux extension—do not install both as competing owners. No Azure execution was performed while authoring.

- [Flux GitHub bootstrap and credential permissions](https://fluxcd.io/flux/installation/bootstrap/github/)
- [Flux impersonation and role-based reconciliation](https://fluxcd.io/flux/components/kustomize/kustomizations/#role-based-access-control)
- [Azure Login with OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
- [ACR private endpoint connectivity](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-link)
- [GitHub self-hosted runner security](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
