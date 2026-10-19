# Lab 5 - Publish to ACR and prove an AKS workload is healthy

**Level:** beginner. **Scenario:** an image exists on your workstation, but that does not mean AKS can pull, schedule or serve it. Establish each boundary separately.

**Outcome:** the supplied API and worker run on the application pool, with successful image pulls, rollout evidence and private management-local HTTP probes.

## Prerequisites and resource boundary

Complete [lab 4](04-node-pools-and-capacity.md). Use the private build VM, Bash at the repository root, Podman and the active TLS-verified Bastion session from lab 3. Human publishing requires registry-scoped AcrPush; Kubernetes deployment uses the operator's existing permissions.

Reuse the existing ACR, pools and synthetic orders application. Image storage, builds, logs and dependency traffic can incur charges, but no additional nodes or Azure services are required. ACR has authenticated public bootstrap access until lab 12. Do not enable its admin user or store an Azure password.

**First workload deployment only:** if `rendered/base` or the workload already exists, inspect the current stage before continuing. Never regenerate rendered files after adding identity/networking changes, and never apply this base after GitOps adoption. [Lab 10's entry paths](10-baseline.md#entry-paths) cover existing environments.

## Directives

### 1. Distinguish publishing from image pulling

**Task:** identify the registry, the human publisher and the kubelet principal. Explain why a successful human login does not fix missing kubelet access.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az acr show --name "$(lab_value AcrName)" \
  --query '{id:id,server:loginServer,sku:sku.name,admin:adminUserEnabled,public:publicNetworkAccess}' -o json
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query identityProfile.kubeletidentity -o json
```

Foundation assigns this registry's AcrPull role to the **kubelet** identity. The application's workload identity accesses Service Bus/Key Vault, not the image-pull path.

If your human publisher lacks AcrPush, have the authorized role-assignment operator grant it at this registry only:

```bash
me=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$me" --assignee-principal-type User \
  --role AcrPush --scope "$(output_value acrId)"
```

Use the human operator flow, not a stored service-principal secret. This foundation uses classic registry RBAC; do not assume AcrPush/AcrPull apply unchanged to an ABAC-enabled registry in another environment.

</details>

### 2. Build and publish the supplied image

**Task:** authenticate using the shared token helper and push one Linux image with the configured tag.

<details>
<summary>Solution</summary>

```bash
bash ./scripts/connect-acr-podman.sh \
  --registry-name "$(lab_value AcrName)" --registry-server "$(lab_value RegistryServer)"
podman build --pull=always --platform linux/amd64 --file ./app/Containerfile \
  --tag "$(lab_value RegistryServer)/order-app:$(lab_value ImageTag)" ./app
podman push "$(lab_value RegistryServer)/order-app:$(lab_value ImageTag)"
```

The helper obtains a short-lived Entra token, verifies the returned registry hostname and passes the token through standard input. Never echo the token or enable shell tracing. Podman's success shows publisher access, not kubelet access.

Record the image reference and push digest. Use a new tag for a genuinely new build rather than silently replacing a known image. Lab 13 introduces the reviewed immutable-digest promotion workflow; do not build an alternative pipeline here.

</details>

### 3. Render once, deploy and inspect the rollout

**Task:** render the deployment-independent base with the real outputs, deploy it and verify the intended replicas have rolled out.

<details>
<summary>Solution</summary>

Run this only for the first application deployment. The guard intentionally stops if rendered state already exists:

```bash
if [[ -e ./rendered/base ]]; then
  printf '%s\n' 'Existing rendered/base: inspect and resume; do not rerender accumulated state.' >&2
  exit 1
fi
bash ./scripts/render-manifests.sh
kubectl apply -k ./rendered/base
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
kubectl get pods -n orders -o wide
kubectl get events -n orders --sort-by=.lastTimestamp
kubectl get pdb -n orders
```

The renderer resolves tokens and checks the Kustomize tree. Retain its output for later identity/networking additions and GitOps adoption. If initial rendering or deployment fails, inspect the error and partially written state; do not delete it or rerun the whole lab blindly. An unchanged, successfully rendered initial base may be reapplied to complete a failed first deployment, but not after later customization.

Require two available API replicas and one worker on the `apps` pool. Compare pod node names with lab 4's node/zone inventory. Inspect the pod/deployment security settings: UID/GID 10001, non-root, read-only root filesystem, dropped capabilities, resource limits and explicit writable `/tmp`.

The worker has no HTTP readiness probe. Running/Ready status alone does not prove it can process a business message; keep that distinction for lab 11.

</details>

### 4. Prove the management-local application path

**Task:** reach the API without creating a public LoadBalancer or ingress.

<details>
<summary>Solution</summary>

Open a second Bash terminal on the **same VM**. Copy the active owner's exact temporary kubeconfig path using the [README shared-session recipe](../README.md#bastion-sessions-and-data-plane-access), verify context, then run:

```bash
kubectl config current-context
kubectl port-forward --address 127.0.0.1 -n orders service/order-api 8080:80
```

Keep that terminal attached. Back in the first VM terminal:

```bash
curl --fail --show-error --max-time 10 http://127.0.0.1:8080/healthz
curl --fail --show-error --max-time 10 http://127.0.0.1:8080/readyz
kubectl logs -n orders deployment/order-api --tail=30
kubectl logs -n orders deployment/order-worker --tail=30
```

Both probes must succeed. Local HTTP inside the port-forward is not proof of real ingress/TLS, Azure dependency health or successful order processing. It uses the Kubernetes management connection; lab 12 later adds ordinary private HTTPS routing. Leave order submission for lab 11. Durable `GET /orders/{id}` is unavailable until lab 21.

For a workstation browser, use the README's explicit Bastion SSH local-forward recipe; do not expose port 8080 on the VM.

</details>

### 5. Diagnose the failing layer

**Task:** explain how you would distinguish a bad image from a bad identity, network path or application configuration. Do not revoke shared ACR access to manufacture a failure.

<details>
<summary>Solution</summary>

```bash
kubectl describe deployment order-api -n orders
kubectl get pods -n orders -o json | jq '.items[] | {
  pod:.metadata.name,node:.spec.nodeName,conditions:.status.conditions,
  containers:.status.containerStatuses}'
```

| Evidence | First investigation |
|---|---|
| `ImagePullBackOff` / registry unauthorized | Exact image/hostname, kubelet principal and registry role scope/propagation |
| Manifest/tag not found | Compare the deployed reference with the actual pushed repository/tag |
| Registry timeout or DNS failure | Registry reachability from nodes; human Podman success is a different path |
| Pending with selector mismatch | Pool labels/taints and scheduler events; do not change identity roles |
| Container exits repeatedly | Container logs, configuration, startup failure and resources |
| Probes succeed, orders fail | Azure dependency/business path; a shallow probe is not a transaction |

Do not patch arbitrary roles, disable TLS or open private endpoints to make a generic error disappear. Preserve the evidence and correct the specific cause.

</details>

## Exit evidence

Record registry/image/digest, human-versus-kubelet roles, completed rollouts, live pool/zone placements, successful probes and retained rendered state. Record what these checks do **not** prove about business completion.

## Customer conversation

<details>
<summary>Model answer: Why does "docker worked on my machine" not prove AKS is ready?</summary>

Image architecture, registry authorization, node networking, resource/placement constraints and runtime configuration differ. Test each boundary with the principal and host that actually use it.

</details>

<details>
<summary>Model answer: Should an image build pipeline have cluster-admin?</summary>

No. Publishing an image requires registry permissions, not Kubernetes access. Later delivery separates a private image publisher from the namespace-scoped GitOps reconciler.

</details>

## Cleanup and next step

Log out the short-lived registry credential when publishing is finished:

```bash
podman logout "$(lab_value RegistryServer)"
```

Stop the port-forward with Ctrl+C when pausing; disconnect the owning Bastion session afterward. Retain the workload, image, settings and `rendered/base` for [lab 6](06-troubleshooting-and-lifecycle.md). Do not delete the registry or re-render just to start a new terminal.

## References

[AKS and ACR integration](https://learn.microsoft.com/azure/aks/cluster-container-registry-integration), [ACR authentication](https://learn.microsoft.com/azure/container-registry/container-registry-authentication), [image management best practices](https://learn.microsoft.com/azure/aks/operator-best-practices-container-image-management), [AKS resource management](https://learn.microsoft.com/azure/aks/developer-best-practices-resource-management). This exercise uses synthetic data and does not establish a production supply chain.
