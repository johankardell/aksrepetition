# Enterprise AKS refresher - 2026

Ten cumulative, hands-on labs plus four optional specialist exercises for an experienced AKS practitioner returning to customer-facing work. No pods-and-services introductory tour: start with a private, identity-enabled platform and end with regional recovery and fleet operations.

**Start with [Lab 1](labs/01-baseline.md).** The guides include commands, expected evidence, controlled failures, recovery instructions, and customer questions with model talking points. Run labs 1-10 in order; later labs depend on earlier state. [Lab 11](labs/11-kaito.md) is an optional extension after lab 6, before final teardown, with separate GPU quota and budget approval. Labs [12](labs/12-rabbitmq-container-storage.md) and [13](labs/13-confidential-compute.md) are self-contained optional exercises that create dedicated clusters and can be run independently. [Lab 14](labs/14-kustomize.md) is a self-contained, offline Kustomize exercise with easy, medium, and hard tiers.

Each task keeps its challenge prompt visible and provides a collapsed **Solution** block with the worked commands, explanations and recovery steps. Expand it when you need help; customer questions have separate collapsed **Model answer** blocks. Prerequisites, safety boundaries and required exit evidence remain visible. Collapsing a block only hides its contents in the rendered guide; it does not make any setup task optional. Use GitHub's rendered Markdown view to expand the blocks.

| Lab | Customer capability | Main technologies |
|---|---|---|
| [1. Enterprise baseline](labs/01-baseline.md) | Establish and justify the platform | Bicep, private AKS, Azure Bastion native tunnel, Entra RBAC, Cilium Overlay, ACR, availability |
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
| [12. RabbitMQ storage density (optional)](labs/12-rabbitmq-container-storage.md) | Evaluate durable messaging placement and disk-attachment pressure | RabbitMQ Cluster Operator, quorum queues, Azure Disk CSI, Azure Container Storage, local NVMe |
| [13. Confidential compute placement (optional)](labs/13-confidential-compute.md) | Mix ordinary and confidential workloads without wasting confidential capacity | AKS node pools, AMD SEV-SNP CVMs, taints, tolerations, node affinity, autoscaling |
| [14. Progressive Kustomize practice (optional)](labs/14-kustomize.md) | Build reusable manifests through increasingly difficult exercises | Bases, overlays, generators, patches, replacements, components |

## Scope and readiness

These are **lab implementation materials, not a certified production landing zone**. The workload/platform path targets supported GA features in Azure public cloud, with one explicitly accepted **preview dependency: native Bastion access to the private AKS API through `aks-preview`**. This is the primary Kubernetes management path in labs 1-10 and the cumulative lab-11 extension; do not claim the whole curriculum is GA-only. Core labs source review: **2026-09-10**; Bastion integration checked **2026-10-01**; optional KAITO extension: **2026-09-14**; optional storage and confidential-compute extensions: **2026-09-23**. Live deployment, regional capacity, end-to-end recovery, and Azure charges have **not** been verified in your subscription. Check the runtime prerequisites before applying anything.

The sample has **no end-user authentication**. Use only synthetic orders and private access until the restricted public-origin capstone. Workload identity authenticates the application to Azure; it does not authenticate the application's customers. A real customer solution needs end-user/API authentication, authorization, abuse controls, data classification, and its own threat model.

The baseline enables private AKS management immediately. ACR, Service Bus and Key Vault start with authenticated public data endpoints during bootstrap; lab 3 creates Private Link and disables those public endpoints. This deliberate intermediate state is **not the finished enterprise baseline**. Do not import business data or treat lab 1 alone as production-ready.

Lab 3 also establishes private ingress DNS and an explicitly approved certificate bundle on the Linux management host. Later Bash load exercises depend on that ordinary HTTPS path; the earlier one-off `curl --resolve --cacert` check alone does not satisfy it.

## Before spending anything

Use a dedicated subscription. Agree the subscription ID, primary and secondary regions, permitted billable footprint, and cleanup owner before running deployment commands. The supplied scripts do not deploy automatically when opened.

| Resource | When introduced | Important cost/availability consideration |
|---|---|---|
| AKS Standard pricing tier, 3 system + 3 application nodes | 1 | Default is six 4-vCPU VMs; allow node-family quota plus upgrade surge and scaling headroom. Zones and VM SKUs must exist in the region. |
| Azure Bastion Standard, two scale units + Standard public IP | 1 / 10 | Included in each regional foundation deployment. Standing charges remain after disconnecting; the public IP belongs to Bastion, not AKS or a management VM. |
| Private Ubuntu build VM, Standard_B2s, 64-GiB Standard SSD | 1 / 10 | Two burstable vCPUs and 4 GiB RAM, plus 2 GiB swap. No VM public IP. Check B-series quota/availability; sustained builds can exhaust CPU credits. Set `BuildVmSize` to an approved larger x64 SKU if needed. |
| ACR Premium, Service Bus Premium (one messaging unit), Key Vault | 1 | Premium dependencies support the private network exercises; Service Bus can be a significant standing cost. |
| Log Analytics | 1 | Ingestion and retention are billable; use synthetic low-volume traffic. |
| Azure Firewall Standard + public IP and hub peering | 1 / 10 | Foundation supplies explicit build-VM egress; lab 3 transitions AKS node egress to it. Firewall standing charges dominate the small VM's cost and continue when the VM is deallocated. One frontend IP is a lab simplification, not production SNAT sizing guidance. |
| Private endpoints and optional separate CI runner | 3-4 | Private DNS/routing serves ACR, Key Vault, Service Bus, application HTTPS and PostgreSQL. Runner registration, isolation and any additional compute remain separately approved. |
| Managed Prometheus, Grafana, Application Insights | 5 | Query, ingestion, retention, and Grafana costs depend on configuration. |
| Extra nodes, NAP comparison, Spot | 6 | Hard-bound load, replica count, and node capacity. Stopping a NAP cluster is unsupported. |
| Defender, PostgreSQL, disk/file storage, backup | 7-8 | Protection, snapshots, storage, and retained backups can keep costing money after workloads stop. |
| Second region, Front Door/WAF and Fleet-related resources | 10 | Deploy only for the capstone and remove deliberately afterward. |
| KAITO GPU inference | 11 (optional) | One desired A100 GPU node; quota/capacity and explicit timed cleanup required. Workspace deletion does not delete GPU node pools. |
| RabbitMQ storage comparison | 12 (optional) | Dedicated AKS cluster with three storage-optimized nodes. Local NVMe is ephemeral; the optional Elastic SAN extension has a substantial minimum provisioned capacity. |
| Confidential compute | 13 (optional) | Dedicated AKS cluster with ordinary and AMD SEV-SNP confidential VM pools. CVM SKU quota, regional availability and idle confidential nodes drive cost. |

Create Azure Cost Management budgets and alerts for the subscription/resource groups before starting. **Budgets are notifications, not spending caps.** See [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) using your agreement and regions, and [budget guidance](https://learn.microsoft.com/azure/cost-management-billing/costs/tutorial-acm-create-budgets). No fixed currency estimate is supplied because region, agreement, duration, and traffic dominate it.

## Tooling, access, and private connectivity

Use Linux with Bash **5+**, GNU coreutils/findutils, `jq`, Python **3.12+**, `curl`, OpenSSL **3+**, OpenSSH (`ssh-keygen`), `ca-certificates`, `dig` (dnsutils/bind-utils), Azure CLI **2.90+**, Bicep, Git, `kubectl` within the supported one-minor skew of the API server, `kubelogin`, Helm, and Podman with **Linux container build capability** on the build machine. Lab 1 pins the reviewed `aks-preview` and `bastion` extensions for native API access. That explicit preview exception does not authorize unrelated preview features. Later guides identify Flux CLI, GitHub runner, and other extension prerequisites.

For the foundation bootstrap workstation, Bash, Git, `jq`, Azure CLI/Bicep and OpenSSH are sufficient; install the Bastion extension before SSH. The provisioned VM supplies the remaining lab tools. Local `kubectl`/kubelogin are needed only if you also use native AKS access directly from that workstation.

An Azure Contributor role alone cannot create RBAC assignments. The bootstrap operator needs resource deployment plus role-assignment authority at the relevant scopes, for example Contributor and Role Based Access Control Administrator in the dedicated subscription. Use an existing Entra security group you belong to for the lab cluster administrator group. Group creation requires separate directory rights; subscription ownership does not grant them. Later labs use separate identities with narrower permissions.

**The primary Kubernetes path is workstation -> Bastion -> private AKS API.** The foundation invokes `infra/bastion.bicep` as its `bastion` module after creating `AzureBastionSubnet`; it does not expose the API or require a jump VM for API access. The foundation what-if and apply confirmation cover Bastion's cost too. For an older existing foundation, lab 1 includes a focused add-on deployment; never replay a progressed foundation just to add Bastion. Native tunneling uses the explicitly approved preview CLI path with ordinary Entra user credentials. Local accounts stay disabled; never add `--admin`, disable TLS verification, or copy a public-endpoint workaround. A standard public Cloud Shell or hosted runner still has no direct private API/ACR connectivity.

### Bastion sessions and data-plane access

After the lab-1 foundation deployment, activate a tunnel in the current shell from the repository root:

```bash
source ./scripts/activate-aks-bastion.sh
```

The sourceable helper starts the lower-level Bastion tunnel as a background child of the current Bash/Zsh session and exports a mode-600 temporary `KUBECONFIG` pointing to localhost while retaining the cluster CA and private FQDN as `tls-server-name`. Keep that shell alive. Do not run `az aks get-credentials` against the active tunnel kubeconfig: it would replace localhost with the unreachable private endpoint. Run `disconnect_aks_bastion` when finished; it stops the exact tunnel PID, removes its temporary files and restores the previous `KUBECONFIG`. A closed terminal normally terminates its child tunnel, but explicit disconnect is the required cleanup path.

For a second terminal on the **same host**, print `printf '%s\n' "$KUBECONFIG"` in the owner shell, then export that exact path in the second terminal. Run `kubectl config current-context` there before the requested port-forward/diagnostic command. Both terminals share the one tunnel; the exported path is unusable after `disconnect_aks_bastion`. Do not copy tunnel kubeconfigs to other hosts, and do not search unrelated processes to guess a port. The existing `bash ./scripts/connect-aks-bastion.sh` foreground/subshell workflow remains available and is useful when lab 10 needs independently owned regional tunnels with distinct ports and contexts.

**Bastion is not a VPN.** It does not route workstation `podman`, `curl`, `psql`, or Azure data-plane CLI calls into the VNet. Foundation therefore includes a private Ubuntu build/management VM in the spoke's management subnet. Access it through Bastion SSH, and run the mixed lab command blocks there with a native AKS API session. Ordinary lab-3 HTTPS, private registry builds, secret operations and SQL then use the VM's own VNet routing/DNS. No VPN or public IP on the VM is required.

**Full-curriculum path:** the build VM uses Azure DNS in the same spoke as AKS and the later service private DNS zone links. Its management subnet is private (`defaultOutboundAccess: false`) and routes internet traffic through the foundation firewall; private spoke traffic stays local. The firewall permits the reviewed package/build destinations only from the management subnet, without broadening node permissions. Bastion's subnet does not inherit this route table. ACR becomes private in lab 3 and PostgreSQL is private when introduced in lab 8; do not enable public endpoints to compensate for missing operator connectivity.

For a peered data-plane/management VNet, link the AKS private DNS zone and service private DNS zones to that VNet, or configure conditional forwarding through Azure DNS Private Resolver. Peering alone does not configure DNS. Do not expose TCP 3389/22 publicly to shortcut access. Build on a Podman-capable Linux host or workstation that can resolve and reach private ACR after lab 3. Keep the Bastion subnet out of the AKS node UDR; Bastion has its own documented routing/NSG requirements.

### Private build VM

Foundation installs Ubuntu 24.04, Python 3.12/venv, rootless Podman, Git/GitHub CLI, Azure CLI 2.90+, Bicep, the reviewed Bastion/AKS extensions, a same-minor `kubectl`, kubelogin, Helm 3, Flux, PostgreSQL client, DNS tools, OpenSSL and Zsh. Binary downloads are checksum-verified. Provisioning waits for cloud-init and a rootless Podman check; failures stop deployment rather than reporting a ready host. Logs are `/var/log/cloud-init-output.log`, and recorded versions are `/var/lib/aks-lab/tool-versions.txt`.

From the original workstation, after foundation completes:

```bash
bash ./scripts/connect-build-vm.sh
```

This uses the separate local `rendered/keys/build-vm` private key and signs in as `labbuilder`. Only the Bastion subnet can initiate SSH; no password login, VM public IP, stored Azure credentials or broad VM managed identity is added. Keep private SSH keys on the workstation.

On the VM, clone this repository using HTTPS (`gh auth login --git-protocol https` first if private). Transfer your existing `local.settings.json` and the **public** `aks.pub` / `build-vm.pub` files into the checkout, without recreating settings or rendered state from a progressed environment. For example, keep a separate workstation terminal running:

```bash
source ./scripts/use-lab.sh
az network bastion tunnel --ids "$(output_value bastionId)" \
  --target-resource-id "$(output_value buildVmId)" --resource-port 22 --port 2222
```

In another workstation terminal, after cloning into `/home/labbuilder/aksrepetition`, verify the SSH host fingerprint through your approved Azure/SSH process, then:

```bash
ssh -p 2222 -i ./rendered/keys/build-vm labbuilder@127.0.0.1 \
  'mkdir -p ~/aksrepetition/rendered/keys; chmod 700 ~/aksrepetition/rendered/keys'
scp -P 2222 -i ./rendered/keys/build-vm ./local.settings.json \
  labbuilder@127.0.0.1:aksrepetition/local.settings.json
scp -P 2222 -i ./rendered/keys/build-vm ./rendered/keys/aks.pub ./rendered/keys/build-vm.pub \
  labbuilder@127.0.0.1:aksrepetition/rendered/keys/
```

Close that transfer tunnel when done. On the VM, from the checkout, sign into Entra interactively and start the API session:

```bash
settings=$(jq -e . ./local.settings.json)
az login --use-device-code --tenant "$(jq -er .TenantId <<< "$settings")"
az account set --subscription "$(jq -er .SubscriptionId <<< "$settings")"
source ./scripts/activate-aks-bastion.sh
```

Do not redeploy foundation or regenerate manifests simply because you moved hosts; transfer the current local rendered/certificate state when continuing labs 1-3. Use a Python virtual environment when installing application dependencies on Ubuntu. If viewing the lab-1 browser UI from the workstation, open an additional Bastion SSH session with `-- -L 127.0.0.1:8080:127.0.0.1:8080`, then run the API port-forward on the VM.

The build VM is not automatically registered as a GitHub runner. Lab 4 still requires an approved ephemeral runner, its OIDC publisher identity, and a separate unprivileged runner account with rootless Podman; never run repository workflows as `labbuilder` with access to the operator's Azure/GitHub credentials. An isolated runner VM is preferable for customer workloads.

Build-host source review **2026-10-01**: [Azure cloud-init](https://learn.microsoft.com/azure/virtual-machines/linux/using-cloud-init), [Bastion native SSH](https://learn.microsoft.com/azure/bastion/connect-vm-native-client-linux), [explicit outbound networking](https://learn.microsoft.com/azure/virtual-network/ip-services/default-outbound-access), [Trusted Launch](https://learn.microsoft.com/azure/virtual-machines/trusted-launch), and [Podman build/SBOM options](https://docs.podman.io/en/v4.9.3/markdown/podman-build.1.html). Pinned tool assets/checksum locations and offline templates were checked; live provisioning and regional capacity remain unverified.

## Bootstrap configuration

From this folder, create the ignored local configuration once. Do not overwrite an existing checkout's settings:

```bash
set -euo pipefail
if [[ -e ./local.settings.json ]]; then
  printf 'Keep existing local.settings.json; review it before resuming.\n'
else
  umask 077
  cat > ./local.settings.json <<'JSON'
{
  "SubscriptionId": "REPLACE",
  "TenantId": "REPLACE",
  "AdminGroupObjectId": "REPLACE",
  "ResourceGroup": "REPLACE",
  "Prefix": "REPLACE",
  "Location": "REPLACE",
  "SecondaryLocation": "REPLACE",
  "VmSize": "Standard_D4ds_v5",
  "BuildVmSize": "Standard_B2s",
  "KubernetesVersion": "REPLACE",
  "Namespace": "orders",
  "ImageTag": "v1",
  "Hostname": "REPLACE"
}
JSON
fi
```

Edit the configuration before running the following block. Choose a private lab FQDN for `Hostname` (for example `orders.aks-lab.test`); lab 3 uses private DNS and a locally trusted self-signed certificate, so public domain ownership is not required. Lab 10 separately requires two owned origin names and public-CA certificates. Select an approved secondary region distinct from `Location` for lab 10. Discover a supported regional Kubernetes patch using the lab 1 preflight commands.

```bash
# Edit all REPLACE values, choose regions and a unique 4-12 character lowercase prefix.
settings=$(jq -e . ./local.settings.json)
az login --tenant "$(jq -er .TenantId <<<"$settings")"
az account set --subscription "$(jq -er .SubscriptionId <<<"$settings")"
az aks get-versions --location "$(jq -er .Location <<<"$settings")" -o table
az vm list-usage --location "$(jq -er .Location <<<"$settings")" -o table
az vm list-skus --location "$(jq -er .Location <<<"$settings")" --size "$(jq -er .VmSize <<<"$settings")" --all -o table
az vm list-skus --location "$(jq -er .Location <<<"$settings")" --size "$(jq -er '.BuildVmSize // "Standard_B2s"' <<<"$settings")" --all -o table
# Select a supported GA patch with a supported upgrade path for lab 9; save it in settings.
az group create --name "$(jq -er .ResourceGroup <<<"$settings")" --location "$(jq -er .Location <<<"$settings")" \
  --tags purpose=aks-enterprise-refresher environment=lab
source ./scripts/use-lab.sh
mkdir -p ./rendered/keys
# Separate node break-glass and build-VM keys. Prompts for passphrases; never overwrite existing keys.
for key in aks build-vm; do
  if [[ -e "./rendered/keys/$key" || -e "./rendered/keys/$key.pub" ]]; then
    [[ -f "./rendered/keys/$key" && -f "./rendered/keys/$key.pub" ]] || {
      printf 'Incomplete key pair: %s; recover it before continuing.\n' "$key" >&2; exit 1;
    }
  else
    ssh-keygen -t rsa -b 4096 -f "./rendered/keys/$key"
  fi
done
```

Example regions are **not** an availability guarantee. Check AKS, Service Bus Premium, the VM family, managed monitoring, PostgreSQL, backup and Fleet requirements before choosing. Check current AKS versions with [the AKS release tracker](https://releases.aks.azure.com/); a listed preview is not a supported GA choice. If quota or regional compatibility blocks a required path, stop and resolve it rather than silently substituting a different service.

All `.sh` helpers under `scripts/`, `ops/` and `advanced/` support execution with Bash 5+ or Zsh 5.9+. The shebang defaults to Bash; choose Zsh explicitly with `zsh ./scripts/<name>.sh --kebab-case-option value`. Execute helpers rather than sourcing them, except for `scripts/use-lab.sh` and the shared `scripts/lib.sh`. The lab command blocks remain Bash-oriented; run `bash` before following those blocks if your terminal uses another shell. Offline helper tests run under both shells when Zsh is installed.

`use-lab.sh` verifies your settings and Azure context; it never silently changes subscriptions or shell options. Source it **from Bash or Zsh** to load `Lab` and `Outputs` as JSON strings. Sourcing does not honor the script's shebang, and running `bash ./scripts/use-lab.sh` or `zsh ./scripts/use-lab.sh` does not load variables into your current shell. A validation error returns a nonzero status so you can correct `local.settings.json` and retry; if you already enabled `set -e`, use `if source ./scripts/use-lab.sh; then echo 'Lab state loaded'; else echo 'Fix the reported error before continuing' >&2; fi` to keep the shell open. Read settings with `lab_value Prefix` and deployment outputs with `output_value registryServer`; use `jq` for other JSON responses. Execute other helpers with `bash ./scripts/<name>.sh --kebab-case-option value` or the equivalent `zsh` command; script filenames use lowercase kebab-case. The helpers obtain generated resource names from the `foundation` deployment. Do not commit local settings, credentials, rendered certificates or evidence containing personal data.

The `aks` RSA key supplies the supported node Linux profile; its passphrase-protected private key is for controlled break-glass access only. The distinct `build-vm` key authenticates Bastion SSH to the build host. Neither authenticates applications, and neither VM nor nodes have a public SSH endpoint. Disabling local Kubernetes administrator credentials is separate from node SSH configuration. Do not enable preview SSH features simply to remove this bootstrap input.

## Repository layout and ownership

- `infra/`: initial foundation (Bastion, private build VM/bootstrap, firewall hub/peering), and focused network deployments.
- `app/`: synthetic order API/frontend and worker in one Python image; dependencies pinned in `requirements.txt`.
- `k8s/`: templates for the initial workload, identity mount, and private ingress/network policies.
- `scripts/`: context, foundation what-if/deployment, native Bastion connection, manifest rendering and lab certificate generation.
- `ops/`, `.github/workflows/`, `gitops/`: delivery, monitoring and scaling assets introduced by labs 4-6.
- `advanced/`: governance, data, upgrade and regional recovery assets introduced by labs 7-10, plus the optional lab-11 KAITO manifests.
- `labs/12-*`, `labs/13-*`, `labs/14-*`: self-contained specialist guides; their manifests are created in disposable local working folders and are not dependencies of the cumulative environment.
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
| Container storage | Azure Container Storage 2.x exposes local NVMe and Elastic SAN through Kubernetes storage classes. It can avoid per-PVC Azure managed-disk attachments, but it does not remove RabbitMQ's one-volume-per-member requirement or make local NVMe durable. |
| Confidential compute | AKS confidential VM pools use supported AMD SEV-SNP VM sizes. Taints repel ordinary pods; tolerations only permit placement, while selectors or affinity deliberately attract confidential workloads. |

References: [AKS baseline](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks), [AKS modes](https://learn.microsoft.com/azure/aks/what-is-aks), [Gateway API](https://learn.microsoft.com/azure/aks/app-routing-gateway-api), [version and OS lifecycle](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions), [NAP](https://learn.microsoft.com/azure/aks/node-auto-provisioning).

## Pausing, resuming, and final cleanup

Save evidence of each lab's exit criteria and Git commit once GitOps exists. Close temporary port-forwards and remove fault injections before pausing, then run `disconnect_aks_bastion`. Resume with `source ./scripts/activate-aks-bastion.sh`, verify the current cluster context, and read the next lab's prerequisites rather than rerunning bootstrap commands. Lab 10 can use the foreground `connect-aks-bastion.sh` workflow for independently owned regional tunnels and merged kubeconfig paths. Disconnecting stops only the local tunnel; Bastion and its public IP continue billing.

To pause build-VM compute, first close API/SSH sessions and any runner, then run `az vm deallocate --ids "$(output_value buildVmId)"` from the original workstation after sourcing `use-lab.sh`. Use `az vm start --ids "$(output_value buildVmId)"` before reconnecting. Guest shutdown alone is not Azure deallocation. The disk, firewall and Bastion still cost money; finish VM-dependent cleanup and retain evidence before final resource-group deletion.

Do not delete cumulative resources after individual labs. If you ran lab 11, complete its Workspace and GPU-pool cleanup before pausing or final teardown. Labs 12 and 13 use their own resource groups and private management prerequisites, not the cumulative Bastion sessions; complete each guide's teardown instead of leaving specialist node pools or storage resources running. Lab 14 is local and disposable. At final teardown, follow lab 10 and lab 8 cleanup first: remove Fleet membership and global routing, stop/delete backup protection using the documented retention process, remove diagnostic/monitoring associations and external role assignments, and handle database/volume backups intentionally. Keep Bastion available until API-dependent cleanup is complete, then disconnect all current-shell tunnels and close any foreground tunnel-owner subshells before deleting their Bastion hosts/public IPs. Inspect every resource group before deletion:

```bash
source ./scripts/use-lab.sh
az resource list --resource-group "$(lab_value ResourceGroup)" -o table
# Only after confirming this is the dedicated disposable group and retaining required evidence:
az group delete --name "$(lab_value ResourceGroup)"
```

The Azure command prompts for confirmation. Repeat only for explicitly identified secondary/comparison groups, never for a shared subscription or wildcard set. Key Vault purge protection deliberately prevents immediate purge/name reuse. Retained backup data, registries, snapshots, Grafana, public IPs, DNS zones, runners, and resources outside the primary group need their own cleanup. Check Cost Management and Resource Graph afterward; deleting Kubernetes namespaces is not Azure teardown.
