# Lab 1 - Build and justify an enterprise AKS baseline

**Level:** intermediate. **Scenario:** you are the platform engineer explaining a new business-application platform to networking, security and application teams.

**Outcome:** a private AKS cluster with repeatable infrastructure, supported software, Entra access, Cilium Overlay networking, separate system/application capacity and a synthetic workload. This is stage one, not yet the completed private-dependency design.

## Prerequisites and resource boundary

Complete the [README bootstrap](../README.md#bootstrap-configuration). Have a routed management workstation/host and private DNS plan. Confirm spending for six worker VMs, AKS Standard tier, ACR Premium, Service Bus Premium and Log Analytics. Check at least 24 vCPUs plus surge headroom in the chosen VM family. Use only the dedicated resource group.

Files: `infra/main.bicep`, `infra/workload-identities.bicep`, `scripts/deploy-foundation.sh`, `app/`, `k8s/base/`. Run these solutions in Bash on a Linux management host at the repository root, with Azure CLI, jq, kubectl, kubelogin, Podman, dig, getent, OpenSSL and curl installed.

Keep strict mode enabled for the following solutions. `use-lab.sh` supplies `Lab` and `Outputs` as JSON strings, `Root` as the absolute repository root, `lab_value KEY` for a settings value, and `output_value KEY` for a foundation output's `.value`. Deployment-derived resource names must be populated by that helper, not guessed. Execute other helpers with `bash ./path/name.sh` and their named `--kebab-case` options.

## Directives

### 1. Explain the platform before deploying

Sketch this request/data path: private administrator -> AKS private API; kubelet identity -> ACR; workload identity -> Azure dependencies; web/API -> Service Bus -> worker. The workload dependencies are prepared here and explored in lab 2.

Inspect `infra/main.bicep`. Identify the **cluster mode**, **pricing tier**, **network plugin/IPAM**, **data plane**, **node OS**, **control-plane exposure**, and three separate managed identity purposes. Explain why "private cluster" says nothing by itself about the data-plane application's public exposure.

Confirm no pod/service/VNet address range overlaps connected networks. Change the template ranges before initial creation if required; do not attempt an unplanned in-place address migration later.

<details>
<summary>Solution</summary>

| Decision | Answer from `infra/main.bicep` |
|---|---|
| Cluster mode and pricing | Manually managed AKS Standard mode, not Automatic; the resource SKU is `Base` with the `Standard` pricing tier. |
| Networking | Azure CNI Overlay (`azure` / `overlay`) with the Cilium data plane. Pods use `192.168.0.0/16`, services `172.20.0.0/16`, and the default spoke `10.40.0.0/16`. Compare all of them with connected networks, including the later firewall hub. |
| Node OS and placement | Azure Linux 3, with three tainted system nodes and three application nodes across the configured zones. Applications select the `apps` pool. |
| Control plane | Private AKS API, public FQDN disabled, managed Entra integration, Azure RBAC, and local cluster accounts disabled. |
| Cluster identity | Manages Azure infrastructure, with Network Contributor on the spoke and Managed Identity Operator on the kubelet identity. |
| Kubelet identity | Pulls images using AcrPull on this registry; it is not the application's Azure identity. |
| Workload identities | Separate API and worker identities exchange projected service-account tokens for Azure tokens. The API sends orders and reads the synthetic vault value; the worker receives orders. |

The administrator reaches the private API through private routing and DNS, then authenticates with Entra and is authorized by Azure RBAC. Independently, kubelets pull the image from ACR. The API accepts a synthetic order, sends it to Service Bus using its workload identity, and the worker consumes it using its own identity. Application traffic does not traverse the Kubernetes management API.

Private control-plane access does not prevent a public LoadBalancer or gateway from exposing an application. Here the initial Service is ClusterIP and access is by port-forward; lab 3 adds internal ingress. The dependency public endpoints are still enabled during bootstrap and are not the finished private-network design.

</details>

### 2. Discover, then select a supported version

**Task:** select a supported GA Kubernetes patch, compatible client and node SKU with enough regional quota and upgrade headroom. Record the choice; do not select a preview or Azure Linux 2.0.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
source ./scripts/use-lab.sh
az aks get-versions --location "$(lab_value Location)" -o table
az vm list-skus --location "$(lab_value Location)" --size "$(lab_value VmSize)" --all -o json
az provider show --namespace Microsoft.ContainerService --query registrationState -o tsv
az provider show --namespace Microsoft.ServiceBus --query registrationState -o tsv
```

Check `restrictions` and zone support in the SKU output; a familiar VM name is not a capacity guarantee. Select a GA patch with a later supported upgrade available for lab 9, rather than a preview. Register required providers if not registered and you have authority:

Check `kubectl version --client` as well. Its minor version must be within one minor of the selected API server throughout the exercise; use a matching client if the local installation is newer. The managed Gateway API bundle is selected by the AKS version, not installed independently from the newest upstream manifest.

```bash
Providers=(
  Microsoft.ContainerService Microsoft.Network Microsoft.ManagedIdentity
  Microsoft.ContainerRegistry Microsoft.KeyVault Microsoft.ServiceBus
  Microsoft.OperationalInsights Microsoft.Insights
)
for provider in "${Providers[@]}"; do
  az provider register --namespace "$provider" --wait
done
```

Record the selected version, region, OS SKU and quota evidence outside Git. Never select Azure Linux 2.0.

Save the chosen patch in `KubernetesVersion` in `local.settings.json` before deployment. A complete answer includes the actual discovery output and your selected value; no hard-coded version can substitute for current regional availability.

</details>

### 3. Review changes, then explicitly deploy

**Task:** inspect the foundation what-if, approve the billable resource inventory, deploy, and show that the resulting configuration matches your design. Do not apply until the resource boundary and cost are approved.

<details>
<summary>Solution</summary>

```bash
bash ./scripts/deploy-foundation.sh
# Inspect the what-if resource inventory and billable choices first.
# At the apply prompt, type the exact ResourceGroup from local.settings.json.
bash ./scripts/deploy-foundation.sh --apply --confirm
source ./scripts/use-lab.sh
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" \
  --query '{version:kubernetesVersion,sku:sku,private:apiServerAccessProfile,network:networkProfile}' -o json
```

Expected: provisioning succeeds; private cluster true; public FQDN disabled; Azure RBAC enabled; Overlay and Cilium selected. Inspect deployment operations if it fails:

```bash
az deployment operation group list -g "$(lab_value ResourceGroup)" -n foundation \
  --query "[?properties.provisioningState=='Failed'].properties.statusMessage" -o json
```

A newly created identity/role can need propagation. Inspect role scope and principal before retrying; do not solve it by assigning Owner to every identity. A failed/canceled foundation deployment may have no outputs: `use-lab.sh` reports that state and loads bootstrap settings only so you can inspect the failure and retry the initial deployment. Generated service names are unavailable until usable outputs exist. A deployment still in progress, malformed metadata, or a successful deployment missing required outputs is a stop condition, not an empty successful baseline.

</details>

### 4. Prove management access uses the intended identity and network

**Task:** demonstrate private DNS/TCP reachability, Entra authorization and healthy node placement from the management host, then contrast access without private connectivity. Do not enable public access or use admin credentials as a workaround.

<details>
<summary>Solution</summary>

From the VNet-connected management host:

```bash
az aks get-credentials -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --overwrite-existing
kubelogin convert-kubeconfig -l azurecli
fqdn=$(az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --query privateFqdn -o tsv)
dig "$fqdn"
getent ahostsv4 "$fqdn"
timeout 10 bash -c 'exec 3<>/dev/tcp/$1/443' bash "$fqdn"
kubectl config current-context
kubectl auth can-i create deployments --namespace orders
kubectl get nodes -L agentpool,topology.kubernetes.io/zone,kubernetes.azure.com/os-sku
```

Expected: private IP resolution, private TCP reachability, authorized Entra access, six Ready nodes across configured zones. Azure RBAC assignments can take time to propagate.

From a host without VNet connectivity, the private endpoint should not be reachable. Do not enable public access or fetch `--admin` credentials to make that test succeed. If the management VNet differs, configure private zone links or DNS forwarding first.

</details>

### 5. Build and deploy the workload

**Task:** build and publish the Linux image, deploy the rendered workload, and demonstrate healthy API probes through private management access. Use only a synthetic workload and grant human push access at registry scope, not subscription scope.

<details>
<summary>Solution</summary>

Before lab 3, ACR's public endpoint remains enabled for authenticated bootstrap. From a Podman/Linux-build-capable workstation, grant yourself **AcrPush** at this registry only if not already authorized:

```bash
me=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$me" --assignee-principal-type User \
  --role AcrPush --scope "$(output_value acrId)"
bash ./scripts/connect-acr-podman.sh --registry-name "$(lab_value AcrName)" --registry-server "$(lab_value RegistryServer)"
podman build --pull=always --platform linux/amd64 --file ./app/Containerfile \
  --tag "$(lab_value RegistryServer)/order-app:$(lab_value ImageTag)" ./app
podman push "$(lab_value RegistryServer)/order-app:$(lab_value ImageTag)"
bash ./scripts/render-manifests.sh
kubectl apply -k ./rendered/base
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
kubectl get pods -n orders -o wide
kubectl get pdb -n orders
```

Use a unique image tag if repeating the build. Lab 4 replaces mutable lab tags with immutable references. ACR has no admin password; kubelet pulls use its own managed identity, not your human login.

In a separate Bash terminal on the management host:

```bash
kubectl port-forward -n orders service/order-api 8080:80
```

Then:

```bash
curl --fail --show-error http://localhost:8080/healthz
curl --fail --show-error http://localhost:8080/readyz
```

Both return success. The root page links to the API explorer. Leave order submission for lab 2. A worker deployment without an HTTP readiness endpoint only proves the process stays running; business health requires queue and processing evidence later.

</details>

### 6. Break scheduling, diagnose it, and restore the baseline

**Task:** in the lab only, make the API select a nonexistent node pool, explain the stalled rollout, and restore it. Preserve the healthy baseline for lab 2; do not delete nodes or add arbitrary capacity to bypass the fault.

<details>
<summary>Solution</summary>

```bash
kubectl patch deployment order-api -n orders --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"missing"}}}}}'
kubectl get pods -n orders
kubectl get events -n orders --sort-by=.lastTimestamp
kubectl describe deployment order-api -n orders
```

Expected: new pods Pending with no matching node selector. Old available replicas may continue serving because the rolling update cannot finish. Explain why adding random capacity does not fix an impossible selector.

**Recovery:**

```bash
kubectl patch deployment order-api -n orders --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"apps"}}}}}'
kubectl rollout status deployment/order-api -n orders --timeout=300s
```

Re-establish the port-forward if its pod was replaced and repeat both probe requests from task 5. The repaired deployment must finish its rollout, not merely keep an old replica available.

</details>

## Exit evidence

Record the private API/DNS evidence, node pool and zone placement, selected software versions, successful image pull, successful probes, and scheduling failure/recovery. Explain why topology spread with `ScheduleAnyway` is a preference, not a hard zone guarantee, and why a PDB controls voluntary disruption rather than every possible outage.

<details>
<summary>Solution: interpreting availability evidence</summary>

`ScheduleAnyway` lets scheduling proceed even if the preferred zone skew cannot be met; inspect actual pod locations rather than inferring them from the manifest. The API PDB permits at most one unavailable replica during supported voluntary evictions. It does not prevent a node/zone failure, and Deployment rolling updates are controlled by the Deployment strategy, not by treating the PDB as a universal outage guard.

</details>

## Customer conversation

<details>
<summary>Model answer: Why Kubernetes instead of Container Apps/App Service?</summary>

Choose AKS when Kubernetes APIs/ecosystem or platform controls justify the ongoing operational investment, not merely because the app is containerized.

</details>

<details>
<summary>Model answer: Should we start with Automatic?</summary>

It reduces node and day-two decisions with managed defaults. Standard is used here to learn controls, not because manual operation is inherently more enterprise-ready. Check required customization against Automatic support.

</details>

<details>
<summary>Model answer: Does Standard mean an SLA covers the application?</summary>

Cluster mode, pricing tier and application SLO are different things. Control-plane availability does not guarantee correct application behavior.

</details>

<details>
<summary>Model answer: Who owns private access and DNS?</summary>

Platform/network teams must provide working client routes and name resolution; Kubernetes RBAC cannot repair DNS.

</details>

## Cleanup and references

Remove the injected selector and stop the port-forward when finished. Keep all resources for lab 2. Do not delete nodes or the resource group between labs.

<details>
<summary>Solution: cumulative cleanup</summary>

Complete task 6's recovery, then confirm the intended placement:

```bash
kubectl get deployment order-api -n orders -o jsonpath='{.spec.template.spec.nodeSelector.agentpool}'
kubectl rollout status deployment/order-api -n orders --timeout=300s
```

The selector must be `apps`. Stop the port-forward with Ctrl+C in the terminal that owns it; start the same task-5 port-forward again when beginning lab 2. Keep the rendered manifests, image and Azure resources.

</details>

Sources reviewed 2026-09-10: [enterprise baseline](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks), [private clusters](https://learn.microsoft.com/azure/aks/private-clusters), [Cilium](https://learn.microsoft.com/azure/aks/azure-cni-powered-by-cilium), [AKS modes](https://learn.microsoft.com/azure/aks/what-is-aks), [supported versions](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions).
