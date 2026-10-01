# Lab 4 — Deliver releases through GitOps and a private supply chain

**Scenario.** A platform team needs auditable releases without giving an image-build pipeline Kubernetes access. Build once, promote an immutable digest, reconcile desired state, and recover through Git.

**Prerequisites/re-entry.** Complete labs 1–3. Use Bash at the repository root on the VNet-connected Linux management host. `local.settings.json`, `foundation`, private DNS, HTTPS application access and working Workload ID must exist. Install the supported Flux CLI, GitHub CLI, Git, Azure CLI, jq, kubectl, kubelogin, dig, getent, OpenSSL and curl on that host; a **separate operator-provisioned Linux runner** needs rootless Podman with SBOM and digest-file support, Bash, jq and Azure CLI. Podman commands below run on a Linux host with private ACR connectivity; they need not run on the management host. Re-export the lab 3 CA bundle variables in each new terminal before ordinary HTTPS calls. An existing private GitHub repository, permission to configure its environments/runners, and a protected `main` branch are required. No command here creates a GitHub repository.

**Ownership/path.** CI → Entra OIDC → ACR private endpoint writes images only. Flux → HTTPS Git with a repository credential pulls manifests; kubelet → ACR with its existing pull identity fetches images. GitHub OIDC is **not** authentication from Flux to a private Git repository. Platform bootstrap reconciles `gitops/clusters/primary`; the **`orders` Kustomization in `flux-system`** reconciles `gitops/clusters/primary/apps/orders`. `orders-test` uses its own namespace, queue and two managed identities. It is still the same cluster and Service Bus namespace, not a production isolation boundary.

**Inventory/permissions/cost.** Reuse AKS, ACR Premium, Service Bus Premium, Firewall and private management connectivity. Add three user-assigned managed identities, three federations and one queue; identities have no hourly compute charge, while runner VM/storage, image storage, registry transfers and extra test replicas do. A human temporarily needs the AKS **Azure Kubernetes Service RBAC Cluster Admin** role for Flux CRDs/bootstrap, not `--admin` credentials. Its standing application role remains namespace scoped. The infrastructure operator needs resource deployment and role-assignment permissions. CI receives only **AcrPush on this registry** (classic RBAC registry mode); it receives no AKS, subscription Contributor, Key Vault or Service Bus role. With ABAC-enabled ACR, use repository-scoped repository writer permissions instead of assuming AcrPush works.

## Numbered directives

### 1. Establish the context and verify private paths

**Challenge:** establish the intended existing private checkout, reload the lab context and verify Entra API access, private ACR connectivity and Flux prerequisites before making changes.

**If this curriculum folder has no `.git`, establish the working checkout before running the block below.** Clone the already-created private repository into an approved directory, then copy the curriculum source files there, including `.gitignore` and `.github`, but excluding `.venv`, `.artifacts` and any `.git` directory from another checkout. Separately carry `local.settings.json` and the **accumulated** `rendered` directory to the new checkout as ignored local state: later steps need the resolved base, CSI patch, policies, private gateway configuration and certificate files. Do not add any of that local state to Git. Change the Bash working directory to the new checkout and inspect `git status --short --ignored` before the first commit. Do not regenerate `rendered/base`, and do not leave the settings behind. If already working in the intended repository, no move is needed.

**Exit evidence:** record the selected remote, Entra access check, private DNS/TCP results and Flux preflight. Verify private settings/rendered files remain ignored; do not initialize or push to an accidental new remote.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
read -r -p 'Existing private GitHub repository (owner/name): ' GitHubRepository
if [[ ! "$GitHubRepository" =~ ^[^/]+/[^/]+$ ]]; then
  printf '%s\n' 'Use owner/name.' >&2
  exit 1
fi
gh repo view "$GitHubRepository" --json nameWithOwner,isPrivate,defaultBranchRef
git remote -v
git status --short
az aks get-credentials -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --overwrite-existing
kubelogin convert-kubeconfig -l azurecli
kubectl auth can-i get deployments -n orders
dig "$(lab_value RegistryServer)"
getent ahostsv4 "$(lab_value RegistryServer)"
timeout 10 bash -c 'exec 3<>/dev/tcp/$1/443' bash "$(lab_value RegistryServer)"
flux version --client
flux check --pre
```

Compare `git remote -v` with `nameWithOwner` from GitHub rather than assuming the current checkout is correct. The Entra-authenticated deployment check should return `yes`; DNS should resolve the registry's private endpoint and TCP 443 should succeed. `flux check --pre` must pass before bootstrap.

```bash
git status --short --ignored
git check-ignore local.settings.json rendered
```

Ignored local state must not appear among staged changes. If a file was previously tracked, `.gitignore` alone will not remove it from Git; stop and repair the checkout before committing.

</details>

### 2. Provision the publisher identity and a real test environment

**Challenge:** provision the delivery identities, federations and isolated test queue, then configure the protected publishing environment and its private-network runner.

**Constraints:** CI gets registry-scoped publishing rights only. Use an ephemeral Linux x64 runner, supported version at least 2.329.0 for the pinned Node.js 24 actions, restricted to this repository. Its VNet/DNS must reach both ACR login and regional data endpoints, with approved outbound access to GitHub Actions, Entra, ARM, upstream OCI registries, package indexes and SBOM scanner images. Registration tokens are short-lived secrets; never commit them or run untrusted PRs on this runner. A normal GitHub-hosted runner cannot reach the registry; a hosted alternative needs explicitly supported private networking and equivalent verification. The artifact workflow targets GitHub.com; GHES needs its own supported artifact-action matrix.

**Exit evidence:** record deployment outputs and role scopes, environment reviewers/main restriction, runner labels/connectivity and the exact federation subject `repo:OWNER/REPO:environment:acr-publish`. Confirm the workflow environment matches and no client secret exists. Repository write access is a powerful supply-chain privilege.

<details>
<summary>Solution</summary>

```bash
az deployment group create -g "$(lab_value ResourceGroup)" -n delivery \
  --template-file ./ops/delivery.bicep \
  --parameters "prefix=$(lab_value Prefix)" "location=$(lab_value Location)" "acrName=$(lab_value AcrName)" \
    "githubRepository=$GitHubRepository" "serviceBusName=$(lab_value ServiceBusName)" \
    "oidcIssuer=$(output_value oidcIssuer)"
Delivery=$(az deployment group show -g "$(lab_value ResourceGroup)" -n delivery --query properties.outputs -o json)
az role assignment list --scope "$(output_value acrId)" --all -o table
```

In the existing repository's **Settings → Environments**, create `acr-publish`, require reviewers and restrict deployments to `main`. Register the runner using GitHub's current registration instructions with labels `self-hosted,Linux,X64,aks-private`.

```bash
gh variable set AZURE_CLIENT_ID --env acr-publish --repo "$GitHubRepository" --body "$(jq -r '.publisherClientId.value' <<< "$Delivery")"
gh variable set AZURE_TENANT_ID --env acr-publish --repo "$GitHubRepository" --body "$(lab_value TenantId)"
gh variable set AZURE_SUBSCRIPTION_ID --env acr-publish --repo "$GitHubRepository" --body "$(lab_value SubscriptionId)"
gh variable set ACR_NAME --env acr-publish --repo "$GitHubRepository" --body "$(lab_value AcrName)"
gh variable set REGISTRY_SERVER --env acr-publish --repo "$GitHubRepository" --body "$(lab_value RegistryServer)"
```

Compare the deployed federation with the exact repository/environment spelling, and the publisher principal with the registry role assignment. The test API and worker identities are separate from the publisher and each other; their queue permissions apply to `orders-test`, not the primary queue.

</details>

### 3. Publish once, record its digest, and mirror platform controller images

**Challenge:** publish a reviewed application image, retain its immutable reference and supply-chain artifacts, and mirror the matching Flux controller images into private ACR.

Review `.github/workflows/publish-image.yml` and commit the authored curriculum to the existing protected repository through its normal review process. The workflow runs on `main` changes under `app` or manual dispatch, **not** pull requests. The image build is `app/Containerfile`, context `app`, linux/amd64. It retains the pushed digest, Podman build metadata and an SPDX SBOM. These artifacts are evidence, not signed provenance or vulnerability admission enforcement.

**Constraints:** mirror only from the VNet-connected Linux Podman host using a separately authorized operator's registry-scoped push permission and the same Flux CLI version as directive 5. Do not use `az acr build` on the default public task pool after registry public access is disabled. Update pinned Actions SHAs through review; also restrict allowed actions, add vulnerability/license checks and add signed provenance/attestations before enforcing them at admission.

**Exit evidence:** retain the reviewed run ID, successful private-endpoint push, `REGISTRY/order-app@sha256:...` artifact and inventory of the four `fluxcd/` controller images.

<details>
<summary>Solution</summary>

```bash
gh workflow run publish-image.yml --repo "$GitHubRepository" --ref main
gh run list --repo "$GitHubRepository" --workflow publish-image.yml --limit 5
read -r -p 'Successful reviewed workflow run ID: ' RunId
if [[ ! "$RunId" =~ ^[0-9]+$ ]]; then
  printf '%s\n' 'Use a numeric workflow run ID.' >&2
  exit 1
fi
gh run view "$RunId" --repo "$GitHubRepository"
# A run-specific directory avoids selecting stale artifacts from another release.
ArtifactDirectory=".artifacts/image/$RunId"
gh run download "$RunId" --repo "$GitHubRepository" --pattern 'order-image-*' --dir "$ArtifactDirectory"
ReferencePaths=$(find "$ArtifactDirectory" -type f -name image-reference.txt)
mapfile -t ReferenceFiles <<< "$ReferencePaths"
if [[ ${#ReferenceFiles[@]} -ne 1 || ! -f "${ReferenceFiles[0]}" ]]; then
  printf '%s\n' 'Expected exactly one image-reference.txt for the selected run.' >&2
  exit 1
fi
Reference=$(< "${ReferenceFiles[0]}")
Reference=${Reference%$'\r'}
Digest=${Reference##*@}
if [[ ! "$Digest" =~ ^sha256:[a-f0-9]{64}$ ]] \
  || [[ "$Reference" != "$(lab_value RegistryServer)/order-app@$Digest" ]]; then
  printf '%s\n' 'No valid image reference for the intended registry/repository.' >&2
  exit 1
fi
printf '%s\n' "$Reference"
```

On the VNet-connected Podman host, using a separately authorized operator's **registry-scoped push permission**, mirror Flux's matching CLI-version controller images into ACR. This avoids node pulls from `ghcr.io` after egress closure:

```bash
# Same Flux CLI version must be used here and in directive 5.
bash ./scripts/connect-acr-podman.sh --registry-name "$(lab_value AcrName)" --registry-server "$(lab_value RegistryServer)"
bash ./ops/mirror-flux-images.sh --registry-server "$(lab_value RegistryServer)"
```

Check the workflow summary and downloaded reference agree, then confirm the mirrored repositories:

```bash
az acr repository list --name "$(lab_value AcrName)" -o table
```

Expect `order-app` and the source, kustomize, helm and notification controllers under `fluxcd/`. The workflow pins Actions to upstream release commit SHAs verified on the review date; an SBOM and unsigned build metadata alone do not establish artifact trust.

</details>

### 4. Transfer application ownership to Git, not live YAML patches

**Challenge:** adopt the accumulated application configuration into namespace-scoped GitOps, retain the primary identity/CSI behavior, and create test desired state with its own identities and queue through review.

**Constraints:** initialization is one-shot. Do not rerun `render-manifests.sh` or commit runtime secrets/settings. Namespaces and reconciler roles belong to the platform root; application reconcilers cannot write namespaces, CRDs or cluster roles. Primary CSI retains SecretProviderClass access, not Kubernetes Secret access; test identities have no Key Vault role. Repair missing Lab 2 prerequisites instead of widening permissions.

Gateway/TLS remain **manually managed by the Lab 3 platform operator**, outside application Flux: `rendered/gateway.yaml` contains Namespace `gateway-system`, Gateway `gateway-system/orders-gateway` with GatewayClass `approuting-istio`, and HTTPRoute `orders` in namespace `orders`. The copied policy permits `gateway-system` to reach API port 8080. Do not copy that cross-namespace manifest into an app Kustomization or apply a namespace override to it; do not copy locally generated TLS private keys into Git. Preserve the platform manifest/inputs and its explicit owner for later gateway updates.

**Exit evidence:** retain the reviewed adoption diff, rendered primary/test manifests and identity/queue comparison. Verify primary CSI/policy resources remain, test CSI references are absent, and verify `/config-version` after adoption in directive 6.

<details>
<summary>Solution</summary>

```bash
# Preserve the accumulated lab 2 identity and lab 3 policy additions.
ls ./rendered/base
kubectl kustomize ./rendered/base
bash ./ops/initialize-gitops.sh
bash ./ops/set-gitops-image.sh --namespace orders-test --digest "$Digest"
kubectl kustomize ./gitops/clusters/primary/apps/orders-test
git diff --stat
git status --short
git switch -c adopt-orders-gitops
git add gitops
git commit -m "Initialize namespace-scoped GitOps application sources"
git push -u origin HEAD
gh pr create --repo "$GitHubRepository" --base main --fill
# After review/merge:
git switch main
git pull --ff-only
```

`orders-test` has test identity client IDs and `QUEUE_NAME=orders-test`; `orders` keeps its original identities/queue. The initialization script copies application/network sources, not runtime secrets or local settings.

The **accumulated** rendered base is the adoption contract. **Do not run `render-manifests.sh` again here**: that would lose Lab 2's identity and Lab 3's policy additions. Initialization copies the complete accumulated directory, including `secret-provider.yaml`, `identity-patch.yaml`, `policies.yaml` and their Kustomize entries. It then removes only the Namespace resource reference from each app Kustomization because Namespace reconciliation belongs to the platform root (the copied Namespace file remains an unreferenced snapshot).

The primary retains the CSI mount; its reconciler Role permits `secretproviderclasses`, not Kubernetes Secret access. `/config-version` continues rereading the synthetic `/mnt/secrets-store/lab-version`; verify it after adoption. The test copy deliberately removes the CSI resource/patch references and files: test identities have no Key Vault role. Lab 2 must already have created `lab-version` and enabled the CSI provider; repair those prerequisites instead of opening Key Vault access.

</details>

### 5. Bootstrap against the existing repository and reduce runtime Git permission

**Challenge:** bootstrap the private-registry Flux controllers against the existing repository, replace bootstrap write access with read-only runtime Git access, and verify all reconcilers before withdrawing temporary human privileges.

Use a short-lived fine-grained GitHub PAT for **this existing repository only**, with Contents read/write, Metadata read, Administration read as required by Flux bootstrap; ensure organization approval/SSO. Do not record a transcript or enable shell tracing while entering credentials. `read -r -s` suppresses echo and keeps the entered value out of shell history; environment values, process arguments and Kubernetes Secrets are still sensitive. The bootstrap needs a temporary branch-policy exception for its generated controller commit, or an approved platform process; remove the exception immediately.

For a **personal-account repository**, add `--personal` to bootstrap. Do not revoke the read-only runtime PAT. Schedule its rotation and alert on `GitRepository Ready=False`. A read-only SSH deploy key requires approved SSH egress, known-host verification, bootstrap write access and separate key rotation; GitHub Actions OIDC is not private Git authentication.

**Exit evidence:** retain Ready conditions and applied revisions for `flux-system`, `orders` and `orders-test`, plus workload rollout results. Protect the privileged root Git path; child service accounts remain constrained. Withdraw the human's temporary cluster-wide role through the existing access/PIM process after bootstrap; later privileged directives belong to the platform operator, never CI.

<details>
<summary>Solution</summary>

```bash
IFS=/ read -r -a parts <<< "$GitHubRepository"
set +x
trap 'unset GITHUB_TOKEN ReadOnlyPat' EXIT
read -r -s -p 'Short-lived bootstrap PAT (hidden): ' GITHUB_TOKEN
printf '\n'
export GITHUB_TOKEN
# Existing repository was verified in directive 1; bootstrap must not create another.
flux bootstrap github --owner="${parts[0]}" --repository="${parts[1]}" \
  --branch=main --path=gitops/clusters/primary --token-auth \
  --registry="$(lab_value RegistryServer)/fluxcd"
unset GITHUB_TOKEN
git pull --ff-only
read -r -s -p 'Separate runtime PAT: this repository, Contents read only (hidden): ' ReadOnlyPat
printf '\n'
flux create secret git flux-system --namespace=flux-system \
  --url="https://github.com/$GitHubRepository" --username=git --password="$ReadOnlyPat"
unset ReadOnlyPat
trap - EXIT
flux reconcile source git flux-system
flux reconcile kustomization flux-system --with-source
flux reconcile kustomization orders-test --with-source
flux get kustomizations
kubectl -n orders-test rollout status deployment/order-api --timeout=300s
kubectl -n orders-test rollout status deployment/order-worker --timeout=300s
```

The source reconciliation after replacing the Secret verifies the read-only credential independently of bootstrap. Use the same Secret replacement command for rotation. Revoke the superseded write-capable bootstrap PAT after confirming successful read-only reconciliation.

`serviceAccountName: orders-reconciler` constrains the two child reconcilers. Ready conditions and the recorded applied revision must correspond to the reviewed Git state, not merely to controller pods being Running.

</details>

### 6. Validate in test and promote the exact same digest by pull request

**Challenge:** exercise the test release, then promote the identical digest through a reviewed PR without rebuilding. Verify primary rollout and the retained CSI-backed configuration endpoint.

**Constraints:** the designated reviewer must approve/merge through the organization's workflow. Stop the test port-forward when finished. Before Lab 8, GET order lookup returns 503 by design; do not treat enqueue acceptance as durable business completion.

**Exit evidence:** retain test request results and processed synthetic IDs, the promotion PR, both primary image references and the current synthetic `/config-version` value.

<details>
<summary>Solution</summary>

In another Bash terminal:

```bash
kubectl -n orders-test port-forward service/order-api 8082:80
```

In the original terminal:

```bash
bash ./ops/invoke-order-load.sh --base-uri http://127.0.0.1:8082 --count 10 --concurrency 1 --output-path .artifacts/test-release.json
kubectl -n orders-test logs deployment/order-worker --tail=30
git switch -c promote-orders-digest
bash ./ops/set-gitops-image.sh --namespace orders --digest "$Digest"
git add gitops
git commit -m "Promote verified orders image digest"
git push -u origin HEAD
gh pr create --repo "$GitHubRepository" --base main --fill
```

Have the designated reviewer approve and merge the PR using the organization's normal workflow. Stop the test port-forward with Ctrl+C. After merge:

```bash
git switch main
git pull --ff-only
flux reconcile kustomization orders --with-source
kubectl -n orders rollout status deployment/order-api --timeout=300s
kubectl -n orders get deployments -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}'
kubectl -n orders exec deployment/order-api -c api -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/config-version').read().decode())"
```

**Evidence:** test requests return 202, test worker logs processed synthetic IDs, both primary deployments use the same verified digest, and primary `/config-version` still returns the current synthetic Key Vault value from Lab 2. Before Lab 8, GET order lookup returns 503 by design; a 202 only proves enqueue acceptance, not durable business completion.

</details>

### 7. Prove drift correction, then introduce and recover a bad release

**Challenge:** demonstrate reconciliation of live drift, inject a nonexistent digest into test through review, distinguish the resulting failure from registry connectivity/RBAC errors, and recover through another reviewed change.

**Constraints:** replica drift is permitted only before Lab 6 gives HPA ownership. Inject the bad image only into `orders-test`, with explicit reviewer approval. For emergency changes suspend only the affected child, audit the change, repair Git and resume; never leave reconciliation suspended as the final state.

**Exit evidence:** record drift before/after reconciliation, the faulty and recovery Git revisions, pod/events and Ready conditions, and a successful recovered rollout. A working endpoint alone is not rollout evidence.

<details>
<summary>Solution</summary>

Before Lab 6 adds autoscalers, manually scaling is a harmless drift test:

```bash
kubectl -n orders scale deployment/order-api --replicas=3
flux reconcile kustomization orders
kubectl -n orders get deployment order-api
```

**Expected:** returns to the Git replica count (normally two). After HPA owns replicas in Lab 6, do **not** use replicas to test Flux drift.

Make an intentionally nonexistent digest only in **test**, through a reviewed change:

```bash
git switch -c inject-bad-test-release
printf -v BadDigest 'sha256:%064d' 0
bash ./ops/set-gitops-image.sh --namespace orders-test --digest "$BadDigest"
git add gitops
git commit -m "Exercise a deliberately missing test image"
git push -u origin HEAD
gh pr create --repo "$GitHubRepository" --base main --fill
```

After approving/merging this explicitly labeled fault:

```bash
git switch main
git pull --ff-only
BadCommit=$(git rev-parse HEAD)
```

Capture the expected health-check failure without terminating the shared Bash session. Allow ten minutes for the CLI, longer than the child's five-minute health-check timeout, so the controller can report its failure. Require a current-generation health condition rather than treating any Flux connection error as the expected result:

```bash
Status=0
Reconciliation=$(flux reconcile kustomization orders-test --with-source --timeout=10m 2>&1) || Status=$?
printf '%s\n' "$Reconciliation"
[[ "$Status" != 0 ]] || { printf '%s\n' 'Expected the missing image to fail reconciliation health checks.' >&2; exit 1; }
kubectl get kustomization orders-test -n flux-system -o json |
  jq -e '. as $object | any(.status.conditions[]?; .type == "Ready" and .status == "False" and .reason == "HealthCheckFailed" and .observedGeneration == $object.metadata.generation)'
```

```bash
kubectl -n orders-test get pods
kubectl -n orders-test get events --sort-by=.lastTimestamp
flux get kustomizations
```

**Troubleshooting injection:** new pods show `ErrImagePull`/`ImagePullBackOff`; old Ready pods may still serve, so a working endpoint alone is not rollout success. Distinguish `manifest unknown` from DNS/TLS/403 registry failures. A `Forbidden` reconciliation error points to the reconciler Role rather than ACR.

Restore the verified digest through a new PR (works for merge, rebase or squash histories; no assumption about merge-commit parentage):

```bash
git switch -c repair-test-release
bash ./ops/set-gitops-image.sh --namespace orders-test --digest "$Digest"
git add gitops
git commit -m "Restore verified test image after controlled failure"
git push -u origin HEAD
gh pr create --repo "$GitHubRepository" --base main --fill
# After approval/merge:
git switch main
git pull --ff-only
flux reconcile kustomization orders-test --with-source
kubectl -n orders-test rollout status deployment/order-api --timeout=300s
```

Record both Git revisions and Ready conditions. For a real incident, suspend only the affected child Kustomization with `flux suspend kustomization orders`; log the emergency change, repair Git, then resume and reconcile. Never leave reconciliation suspended as the final state.

</details>

### 8. Capture handover evidence and control costs

**Challenge:** produce a handover of release state, ownership and remaining billable resources; distinguish a mid-course handover from final teardown.

**Constraints:** keep primary GitOps/workloads for labs 5–10. Stop ephemeral runner capacity after builds while preserving its registration/security process. Test replicas consume capacity. Final teardown must occur only at course end while the private API remains reachable; do not prune `orders` mid-course. Suspending Flux neither stops workloads nor removes charges.

**Exit evidence:** capture reconciler revisions/readiness, primary/test workload state, registry inventory, runner disposition, named platform/application owners and the ordered final-cleanup plan.

<details>
<summary>Solution</summary>

```bash
flux get all --all-namespaces
kubectl -n orders get deployments,services,pdb
kubectl -n orders-test get deployments,services
az acr repository show-tags --name "$(lab_value AcrName)" --repository order-app -o table
```

Keep the primary GitOps controller/workloads for labs 5–10. Stop ephemeral runner capacity after builds, preserving its registration/security process rather than leaving a privileged shared machine idle. Test resources may be retained for later promotion; their replicas consume real capacity.

For **final teardown only**, remove child Kustomizations from the platform Git root and merge; wait for pruning, then uninstall Flux with `flux uninstall --namespace=flux-system` while the private API is reachable. Revoke the runtime Git credential, delete runner registrations/environment federation, and follow root resource-group cleanup. **Suspending Flux does not stop workloads or remove charges.** Do not prune the `orders` namespace mid-course; later labs depend on its identities, policies and data.

The handover should tie the reviewed Git revision and image digest to the test/promotion evidence, identify who rotates the runtime Git credential and who owns gateway/TLS, and state which test replicas and runner resources remain billable. Tag inventory alone is not the deployed release record: retain the digest and keep its registry content available for rollback.

</details>

## Customer discovery and model answers

<details>
<summary>Model answer: Why pull delivery rather than a CI kubeconfig?</summary>

CI proves/builds an artifact; an in-cluster controller reconciles it. This reduces pipeline access to the private API, but the Git root and Flux service accounts remain privileged assets.

</details>

<details>
<summary>Model answer: Is GitHub OIDC completely secretless delivery?</summary>

It removes the CI-to-Azure client secret. Private Git authentication, registry pull identity, TLS keys and application secrets have separate lifecycles.

</details>

<details>
<summary>Model answer: Can an untrusted PR use our private runner safely?</summary>

Not by default. It could steal reachable credentials or pivot into the VNet. Isolate ephemeral runners, avoid PR triggers and require protected environment approvals.

</details>

<details>
<summary>Model answer: Is orders-test production-like isolation?</summary>

It exercises separate desired state, queue and identities, but shares nodes, cluster administrators and a Service Bus namespace. Stronger trust/compliance boundaries justify separate clusters/subscriptions.

</details>

<details>
<summary>Model answer: Can someone overwrite the image tag?</summary>

Tags are mutable unless policy prevents it; workloads use a digest. Protect ACR write access and retention so the referenced digest remains available.

</details>

<details>
<summary>Model answer: What is a safe hotfix?</summary>

Declare incident ownership; suspend the smallest reconciliation scope, make an audited temporary fix, commit the durable correction, resume and verify.

</details>

## References and support

Source review: **2026-09-10**. Required path: supported Flux v2 APIs, stable Kubernetes APIs, Entra federation and GA private ACR. Flux is upstream self-managed here, **not** the Azure Flux extension—do not install both as competing owners. No Azure execution was performed while authoring.

- [Flux GitHub bootstrap and credential permissions](https://fluxcd.io/flux/installation/bootstrap/github/)
- [Flux impersonation and role-based reconciliation](https://fluxcd.io/flux/components/kustomize/kustomizations/#role-based-access-control)
- [Azure Login with OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
- [ACR private endpoint connectivity](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-link)
- [GitHub self-hosted runner security](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
