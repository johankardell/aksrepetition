# Lab 1 - Build and justify an enterprise AKS baseline

**Level:** intermediate. **Scenario:** you are the platform engineer explaining a new business-application platform to networking, security and application teams.

**Outcome:** a private AKS cluster with repeatable infrastructure, supported software, Entra access, Cilium Overlay networking, separate system/application capacity and a synthetic workload. This is stage one, not yet the completed private-dependency design.

## Prerequisites and resource boundary

Complete the [README bootstrap](../README.md#bootstrap-configuration). Have a routed management workstation/host and private DNS plan. Confirm spending for six worker VMs, AKS Standard tier, ACR Premium, Service Bus Premium and Log Analytics. Check at least 24 vCPUs plus surge headroom in the chosen VM family. Use only the dedicated resource group.

Files: `infra\main.bicep`, `infra\workload-identities.bicep`, `scripts\Deploy-Foundation.ps1`, `app\`, `k8s\base\`.

## Directives

### 1. Explain the platform before deploying

Sketch this request/data path: private administrator -> AKS private API; kubelet identity -> ACR; workload identity -> Azure dependencies; web/API -> Service Bus -> worker. The workload dependencies are prepared here and explored in lab 2.

Inspect `infra\main.bicep`. Identify the **cluster mode**, **pricing tier**, **network plugin/IPAM**, **data plane**, **node OS**, **control-plane exposure**, and three separate managed identity purposes. Explain why "private cluster" says nothing by itself about the data-plane application's public exposure.

Confirm no pod/service/VNet address range overlaps connected networks. Change the template ranges before initial creation if required; do not attempt an unplanned in-place address migration later.

### 2. Discover, then select a supported version

```powershell
. .\scripts\Use-Lab.ps1
az aks get-versions --location $Lab.Location -o table
az vm list-skus --location $Lab.Location --size $Lab.VmSize --all -o json
az provider show --namespace Microsoft.ContainerService --query registrationState -o tsv
az provider show --namespace Microsoft.ServiceBus --query registrationState -o tsv
```

Check `restrictions` and zone support in the SKU output; a familiar VM name is not a capacity guarantee. Select a GA patch with a later supported upgrade available for lab 9, rather than a preview. Register required providers if not registered and you have authority:

Check `kubectl version --client` as well. Its minor version must be within one minor of the selected API server throughout the exercise; use a matching client if the local installation is newer. The managed Gateway API bundle is selected by the AKS version, not installed independently from the newest upstream manifest.

```powershell
foreach ($provider in @(
  'Microsoft.ContainerService','Microsoft.Network','Microsoft.ManagedIdentity',
  'Microsoft.ContainerRegistry','Microsoft.KeyVault','Microsoft.ServiceBus',
  'Microsoft.OperationalInsights','Microsoft.Insights'
)) { az provider register --namespace $provider --wait }
```

Record the selected version, region, OS SKU and quota evidence outside Git. Never select Azure Linux 2.0.

### 3. Review changes, then explicitly deploy

```powershell
.\scripts\Deploy-Foundation.ps1
# Inspect the what-if resource inventory and billable choices first.
.\scripts\Deploy-Foundation.ps1 -Apply -Confirm
. .\scripts\Use-Lab.ps1
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName `
  --query '{version:kubernetesVersion,sku:sku,private:apiServerAccessProfile,network:networkProfile}' -o json
```

Expected: provisioning succeeds; private cluster true; public FQDN disabled; Azure RBAC enabled; Overlay and Cilium selected. Inspect deployment operations if it fails:

```powershell
az deployment operation group list -g $Lab.ResourceGroup -n foundation `
  --query "[?properties.provisioningState=='Failed'].properties.statusMessage" -o json
```

A newly created identity/role can need propagation. Inspect role scope and principal before retrying; do not solve it by assigning Owner to every identity.

### 4. Prove management access uses the intended identity and network

From the VNet-connected management host:

```powershell
az aks get-credentials -g $Lab.ResourceGroup -n $Lab.ClusterName --overwrite-existing
kubelogin convert-kubeconfig -l azurecli
$fqdn = az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query privateFqdn -o tsv
Resolve-DnsName $fqdn
Test-NetConnection $fqdn -Port 443
kubectl config current-context
kubectl auth can-i create deployments --namespace orders
kubectl get nodes -L agentpool,topology.kubernetes.io/zone,kubernetes.azure.com/os-sku
```

Expected: private IP resolution, private TCP reachability, authorized Entra access, six Ready nodes across configured zones. Azure RBAC assignments can take time to propagate.

From a host without VNet connectivity, the private endpoint should not be reachable. Do not enable public access or fetch `--admin` credentials to make that test succeed. If the management VNet differs, configure private zone links or DNS forwarding first.

### 5. Build and deploy the workload

Before lab 3, ACR's public endpoint remains enabled for authenticated bootstrap. From a Docker/Linux-build-capable workstation, grant yourself **AcrPush** at this registry only if not already authorized:

```powershell
$me = az ad signed-in-user show --query id -o tsv
az role assignment create --assignee-object-id $me --assignee-principal-type User `
  --role AcrPush --scope $Outputs.acrId.value
az acr login --name $Lab.AcrName
docker build --platform linux/amd64 --tag "$($Lab.RegistryServer)/order-app:$($Lab.ImageTag)" .\app
docker push "$($Lab.RegistryServer)/order-app:$($Lab.ImageTag)"
.\scripts\Render-Manifests.ps1
kubectl apply -k .\rendered\base
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
kubectl get pods -n orders -o wide
kubectl get pdb -n orders
```

Use a unique image tag if repeating the build. Lab 4 replaces mutable lab tags with immutable references. ACR has no admin password; kubelet pulls use its own managed identity, not your human login.

In a separate PowerShell window on the management host:

```powershell
kubectl port-forward -n orders service/order-api 8080:80
```

Then:

```powershell
Invoke-RestMethod http://localhost:8080/healthz
Invoke-RestMethod http://localhost:8080/readyz
```

Both return success. The root page links to the API explorer. Leave order submission for lab 2. A worker deployment without an HTTP readiness endpoint only proves the process stays running; business health requires queue and processing evidence later.

### 6. Break scheduling, diagnose it, and restore the baseline

```powershell
kubectl patch deployment order-api -n orders --type merge `
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"missing"}}}}}'
kubectl get pods -n orders
kubectl get events -n orders --sort-by=.lastTimestamp
kubectl describe deployment order-api -n orders
```

Expected: new pods Pending with no matching node selector. Old available replicas may continue serving because the rolling update cannot finish. Explain why adding random capacity does not fix an impossible selector.

**Solution:**

```powershell
kubectl patch deployment order-api -n orders --type merge `
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"agentpool":"apps"}}}}}'
kubectl rollout status deployment/order-api -n orders --timeout=300s
```

## Exit evidence

Record the private API/DNS evidence, node pool and zone placement, selected software versions, successful image pull, successful probes, and scheduling failure/recovery. Explain why topology spread with `ScheduleAnyway` is a preference, not a hard zone guarantee, and why a PDB controls voluntary disruption rather than every possible outage.

## Customer conversation

| Ask the customer | Model talking point |
|---|---|
| Why Kubernetes instead of Container Apps/App Service? | Choose AKS when Kubernetes APIs/ecosystem or platform controls justify the ongoing operational investment, not merely because the app is containerized. |
| Should we start with Automatic? | It reduces node and day-two decisions with managed defaults. Standard is used here to learn controls, not because manual operation is inherently more enterprise-ready. Check required customization against Automatic support. |
| Does Standard mean an SLA covers the application? | Cluster mode, pricing tier and application SLO are different things. Control-plane availability does not guarantee correct application behavior. |
| Who owns private access and DNS? | Platform/network teams must provide working client routes and name resolution; Kubernetes RBAC cannot repair DNS. |

## Cleanup and references

Remove the injected selector and stop the port-forward when finished. Keep all resources for lab 2. Do not delete nodes or the resource group between labs.

Sources reviewed 2026-09-10: [enterprise baseline](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks), [private clusters](https://learn.microsoft.com/azure/aks/private-clusters), [Cilium](https://learn.microsoft.com/azure/aks/azure-cni-powered-by-cilium), [AKS modes](https://learn.microsoft.com/azure/aks/what-is-aks), [supported versions](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions).
