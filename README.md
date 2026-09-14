# Enterprise AKS refresher - 2026

Ten cumulative, hands-on labs plus an optional KAITO GPU-inference exercise for an experienced AKS practitioner returning to customer-facing work. No pods-and-services introductory tour: start with a private, identity-enabled platform and end with regional recovery and fleet operations.

**Start with [Lab 1](labs/01-baseline.md).** The guides include commands, expected evidence, controlled failures, recovery instructions, and customer questions with model talking points. Run labs 1-10 in order; later labs depend on earlier state. [Lab 11](labs/11-kaito.md) is an optional extension after lab 6, before final teardown, with separate GPU quota and budget approval.

Each task keeps its challenge prompt visible and provides a collapsed **Solution** block with the worked commands, explanations and recovery steps. Expand it when you need help; customer questions have separate collapsed **Model answer** blocks. Prerequisites, safety boundaries and required exit evidence remain visible. Collapsing a block only hides its contents in the rendered guide; it does not make any setup task optional. Use GitHub's rendered Markdown view to expand the blocks.

| Lab | Customer capability | Main technologies |
|---|---|---|
| [1. Enterprise baseline](labs/01-baseline.md) | Establish and justify the platform | Bicep, private AKS, Entra RBAC, Cilium Overlay, ACR, availability |
| [2. Identity and integration](labs/02-identity.md) | Access Azure without stored application credentials | Workload ID, Service Bus, Key Vault CSI, rotation |
| [3. Private networking and publishing](labs/03-networking.md) | Publish applications while isolating dependencies | Private Link/DNS, hub/spoke, Azure Firewall, Gateway API, TLS |
| [4. GitOps delivery](labs/04-gitops.md) | Deliver auditable, recoverable changes | GitHub Actions OIDC, private ACR, Flux, immutable releases |
| [5. Observability](labs/05-observability.md) | Diagnose user-impacting failures | Managed Prometheus, Grafana, logs, OpenTelemetry, SLOs |
| [6. Scaling and cost](labs/06-scaling.md) | Handle variable API and queue demand | HPA, KEDA, cluster autoscaler, NAP comparison, Spot |
| [7. Multi-team governance](labs/07-governance.md) | Onboard teams with enforced boundaries | Azure/Kubernetes RBAC, quotas, PSA, Azure Policy, Defender |
| [8. Data and recovery](labs/08-data-recovery.md) | Choose and recover durable state | PostgreSQL, CSI Disk/Files, AKS Backup, database recovery |
| [9. Lifecycle management](labs/09-upgrades.md) | Stay supported while managing disruption | Upgrades, node images, maintenance, PDBs, surge capacity |
| [10. Multi-region capstone](labs/10-multi-region.md) | Recover the service and manage multiple clusters | Front Door/WAF, Fleet, regional failover, RTO/RPO, failback |
| [11. KAITO inference (optional)](labs/11-kaito.md) | Operate and evaluate a private self-hosted model | AI toolchain operator, GPU provisioning, Phi-4-mini, vLLM, inference isolation |

## Scope and readiness

These are **lab implementation materials, not a certified production landing zone**. The required path is intended for supported GA features in Azure public cloud. Core labs source review: **2026-09-10**; optional KAITO extension: **2026-09-14**. Live deployment, regional capacity, end-to-end recovery, and Azure charges have **not** been verified in your subscription. Check the runtime prerequisites before applying anything.

The sample has **no end-user authentication**. Use only synthetic orders and private access until the restricted public-origin capstone. Workload identity authenticates the application to Azure; it does not authenticate the application's customers. A real customer solution needs end-user/API authentication, authorization, abuse controls, data classification, and its own threat model.

The baseline enables private AKS management immediately. ACR, Service Bus and Key Vault start with authenticated public data endpoints during bootstrap; lab 3 creates Private Link and disables those public endpoints. This deliberate intermediate state is **not the finished enterprise baseline**. Do not import business data or treat lab 1 alone as production-ready.

Lab 3 also establishes private ingress DNS and explicit current-user certificate trust on the Windows management workstation. Later PowerShell load exercises depend on that ordinary HTTPS path; the earlier one-off `curl --resolve --cacert` check alone does not satisfy it.

## Before spending anything

Use a dedicated subscription. Agree the subscription ID, primary and secondary regions, permitted billable footprint, and cleanup owner before running deployment commands. The supplied scripts do not deploy automatically when opened.

| Resource | When introduced | Important cost/availability consideration |
|---|---|---|
| AKS Standard pricing tier, 3 system + 3 application nodes | 1 | Default is six 4-vCPU VMs; allow node-family quota plus upgrade surge and scaling headroom. Zones and VM SKUs must exist in the region. |
| ACR Premium, Service Bus Premium (one messaging unit), Key Vault | 1 | Premium dependencies support the private network exercises; Service Bus can be a significant standing cost. |
| Log Analytics | 1 | Ingestion and retention are billable; use synthetic low-volume traffic. |
| Private endpoints, Azure Firewall Standard + public IP, peering | 3 | Firewall has standing and data-processing charges. One frontend IP is a lab simplification, not production SNAT sizing guidance. |
| VNet-connected management/build infrastructure | prerequisite / 4 | Supply a routed workstation or a private management/runner host. VPN/Bastion/VM/NAT costs are separate. |
| Managed Prometheus, Grafana, Application Insights | 5 | Query, ingestion, retention, and Grafana costs depend on configuration. |
| Extra nodes, NAP comparison, Spot | 6 | Hard-bound load, replica count, and node capacity. Stopping a NAP cluster is unsupported. |
| Defender, PostgreSQL, disk/file storage, backup | 7-8 | Protection, snapshots, storage, and retained backups can keep costing money after workloads stop. |
| Second region, Front Door/WAF and Fleet-related resources | 10 | Deploy only for the capstone and remove deliberately afterward. |
| KAITO GPU inference | 11 (optional) | One desired A100 GPU node; quota/capacity and explicit timed cleanup required. Workspace deletion does not delete GPU node pools. |

Create Azure Cost Management budgets and alerts for the subscription/resource groups before starting. **Budgets are notifications, not spending caps.** See [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) using your agreement and regions, and [budget guidance](https://learn.microsoft.com/azure/cost-management-billing/costs/tutorial-acm-create-budgets). No fixed currency estimate is supplied because region, agreement, duration, and traffic dominate it.

## Tooling, access, and private connectivity

Use PowerShell **7.4+**, Azure CLI **2.86+** (Gateway API flags), Bicep, Git, `kubectl` within the supported one-minor skew of the API server, `kubelogin`, Helm, and Docker with **Linux container build capability** on the build machine. Later guides identify Flux CLI, GitHub runner, and Azure CLI extension prerequisites. Do not install preview extensions as a workaround for an unsupported required feature.

An Azure Contributor role alone cannot create RBAC assignments. The bootstrap operator needs resource deployment plus role-assignment authority at the relevant scopes, for example Contributor and Role Based Access Control Administrator in the dedicated subscription. Use an existing Entra security group you belong to for the lab cluster administrator group. Group creation requires separate directory rights; subscription ownership does not grant them. Later labs use separate identities with narrower permissions.

**Management connectivity is a hard prerequisite, not a public-API fallback.** Use either a VPN/ExpressRoute-connected workstation or a host in the lab management subnet. The host needs PowerShell/CLI/kubelogin/kubectl and DNS resolution of the private AKS zone. A standard public Cloud Shell session or a public GitHub hosted runner cannot reach the private API/ACR by default. If no management path exists, provision your organization's approved VPN or private host/Bastion pattern first, using [AKS private-cluster guidance](https://learn.microsoft.com/azure/aks/private-clusters).

For a peered management VNet, link the AKS private DNS zone and the three service private DNS zones to that VNet, or configure conditional forwarding through Azure DNS Private Resolver. Peering alone does not configure DNS. Do not expose TCP 3389/22 publicly to shortcut access. Build on a Docker-capable Linux host or workstation that can also resolve and reach private ACR after lab 3.

## Bootstrap configuration

From this folder:

```powershell
Copy-Item .\local.settings.example.json .\local.settings.json
# Edit all REPLACE values, choose regions and a unique 4-12 character lowercase prefix.
$settings = Get-Content .\local.settings.json -Raw | ConvertFrom-Json
az login --tenant $settings.TenantId
az account set --subscription $settings.SubscriptionId
az aks get-versions --location swedencentral -o table
az vm list-usage --location swedencentral -o table
az vm list-skus --location swedencentral --size Standard_D4ds_v5 --all -o table
# Select a supported GA patch with a supported upgrade path for lab 9; save it in settings.
az group create --name $settings.ResourceGroup --location $settings.Location `
  --tags purpose=aks-enterprise-refresher environment=lab
. .\scripts\Use-Lab.ps1
New-Item .\rendered\keys -ItemType Directory -Force | Out-Null
# Only on first setup: ssh-keygen prompts for a passphrase. Do not overwrite an existing key.
ssh-keygen -t rsa -b 4096 -f .\rendered\keys\aks
```

Example regions are **not** an availability guarantee. Check AKS, Service Bus Premium, the VM family, managed monitoring, PostgreSQL, backup and Fleet requirements before choosing. Check current AKS versions with [the AKS release tracker](https://releases.aks.azure.com/); a listed preview is not a supported GA choice. If quota or regional compatibility blocks a required path, stop and resolve it rather than silently substituting a different service.

`Use-Lab.ps1` verifies your Azure context; it never silently changes subscriptions. It obtains generated resource names from the `foundation` deployment. Do not commit local settings, credentials, rendered certificates or evidence containing personal data.

The RSA public key supplies the supported node Linux profile; the passphrase-protected private key is for controlled break-glass access only, not application authentication. Nodes have no public SSH endpoint. Disabling local Kubernetes administrator credentials is separate from node SSH configuration. Do not enable preview SSH features simply to remove this bootstrap input.

## Repository layout and ownership

- `infra/`: initial foundation and focused network deployments.
- `app/`: synthetic order API/frontend and worker in one Python image; dependencies pinned in `requirements.txt`.
- `k8s/`: templates for the initial workload, identity mount, and private ingress/network policies.
- `scripts/`: context, foundation what-if/deployment, manifest rendering and lab certificate generation.
- `ops/`, `.github/workflows/`, `gitops/`: delivery, monitoring and scaling assets introduced by labs 4-6.
- `advanced/`: governance, data, upgrade and regional recovery assets introduced by labs 7-10, plus the optional lab-11 KAITO manifests.
- `rendered/`, `evidence/`, `.artifacts/`: local generated output, excluded from Git.

**Ownership changes are explicit.** Labs 1-3 use imperative commands and rendered templates to expose the mechanics. Lab 4 adopts the workload into GitOps. After adoption, change the Git source or explicitly suspend/resume the named Flux reconciliation for a fault exercise. Platform infrastructure stays outside the application's Flux permissions.

`infra/main.bicep` is the **initial bootstrap snapshot**, not a continuously reconciled production platform module. Later labs intentionally change outbound type, public-network flags, add-ons, autoscaling, and versions through focused commands. **Do not redeploy the initial foundation over a progressed cluster:** that would request old values and can overwrite network/subnet configuration. For a production implementation, fold the final selected settings into owned infrastructure modules and review what-if before every application. A second-region bootstrap is a new resource group, not a replay against the progressed primary.

## Application behavior worth understanding

`POST /orders` returns 202 after Service Bus accepts a message, not after processing commits. The worker uses token authentication over AMQP WebSockets (TCP 443). It completes the message only after its processing step succeeds.

Before lab 8, deduplication is only in memory **per worker process** and disappears on restart; multiple replicas can both process a repeated ID. Do not infer durable exactly-once processing. Lab 8 adds a PostgreSQL primary key and transaction before message completion. This supports idempotent database effects, but does not magically make arbitrary external side effects exactly-once. Reusing an ID with a different item is rejected to the dead-letter queue.

`GET /orders/{id}` intentionally returns 503 before lab 8, then queries durable state. `/healthz` and `/readyz` are process/readiness signals, not deep dependency probes. `/config-version` exposes only the deliberately synthetic CSI `lab-version` value; never map a real secret there.

## What changed while you were away

| Area | Baseline used here |
|---|---|
| Ingress | Managed Gateway API, not a fresh ingress-nginx installation. Upstream ingress-nginx maintenance ended March 2026; Microsoft's managed NGINX critical-patch support runs through November 2026. |
| Operating system | Supported Azure Linux 3 node images, not Azure Linux 2.0. Discover current Kubernetes patch support rather than copying a stale minor version. |
| Networking | Azure CNI Overlay powered by Cilium; compare pod-subnet IPAM when workloads need directly routable pod IPs. |
| Identity | Managed cluster/kubelet identities and Entra Workload ID; no legacy pod identity or stored service-principal secrets. |
| Operations | Compare AKS Automatic and NAP with manual platform control, including actual limitations and support boundaries. |
| Multi-cluster | Fleet orchestrates supported cluster operations; it does not replicate application data or replace a regional failover design. |
| Self-hosted AI | KAITO's managed AKS add-on reconciles model workspaces and GPU capacity; model quality, API security, GPU spend and cleanup remain customer responsibilities. |

References: [AKS baseline](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks), [AKS modes](https://learn.microsoft.com/azure/aks/what-is-aks), [Gateway API](https://learn.microsoft.com/azure/aks/app-routing-gateway-api), [version and OS lifecycle](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions), [NAP](https://learn.microsoft.com/azure/aks/node-auto-provisioning).

## Pausing, resuming, and final cleanup

Save evidence of each lab's exit criteria and Git commit once GitOps exists. Reconnect with `Use-Lab.ps1`, verify the current cluster context, and read the next lab's prerequisites rather than rerunning all bootstrap commands. Remove temporary fault injections before pausing.

Do not delete cumulative resources after individual labs. If you ran lab 11, complete its Workspace and GPU-pool cleanup before pausing or final teardown. At final teardown, follow lab 10 and lab 8 cleanup first: remove Fleet membership and global routing, stop/delete backup protection using the documented retention process, remove diagnostic/monitoring associations and external role assignments, and handle database/volume backups intentionally. Inspect every resource group before deletion:

```powershell
. .\scripts\Use-Lab.ps1
az resource list --resource-group $Lab.ResourceGroup -o table
# Only after confirming this is the dedicated disposable group and retaining required evidence:
az group delete --name $Lab.ResourceGroup
```

The Azure command prompts for confirmation. Repeat only for explicitly identified secondary/comparison groups, never for a shared subscription or wildcard set. Key Vault purge protection deliberately prevents immediate purge/name reuse. Retained backup data, registries, snapshots, Grafana, public IPs, DNS zones, runners, and resources outside the primary group need their own cleanup. Check Cost Management and Resource Graph afterward; deleting Kubernetes namespaces is not Azure teardown.
