# Operations handbook

Canonical setup, session, review, evidence and teardown procedures for the [curriculum](../README.md).
Run all command blocks from the **repository root**, not from `docs/`. Start with your lab's
contract and the [state and ownership map](state.md); this handbook is a reference, not a
second sequence to execute from top to bottom.

| Need | Procedure |
|---|---|
| Approve a deployment | [Cost inventory](#before-spending-anything) and [bootstrap](#bootstrap-configuration) |
| Connect or reconnect | [API sessions](#bastion-sessions-and-data-plane-access), [private build VM](#private-build-vm), [HTTPS trust](#ordinary-https-sessions) |
| Change desired state | [Reviewed Git changes](#reviewed-git-changes) |
| Record progress or stop | [Evidence contract](#evidence-and-completion), [pause/cleanup](#pausing-resuming-and-final-cleanup) |
| Resolve a prerequisite | [Tooling/support](#tooling-and-support), [early go-no-go decisions](#early-go-no-go-decisions) |

### Existing learner migration

This table records an earlier edition's numbering migration, **not a new renumbering in this
restructure**. Existing guide numbers and URLs are preserved. Azure resources, settings,
rendered state and GitOps directories need **no migration or redeployment**.

| Former numbers | Current numbers | Topics retained in order |
|---|---|---|
| 1-6 | 10-15 | Baseline, identity, networking, GitOps, observability, scaling |
| 7-10 | 20-23 | Governance, data recovery, lifecycle, multi-region |
| 11-13 | 24-26 | KAITO, RabbitMQ, confidential compute |
| 14 | 27 | Progressive Kustomize |

## Scope and readiness

These are **lab implementation materials, not a certified production landing zone**. The workload/platform path targets supported GA features in Azure public cloud, with one explicitly accepted **preview dependency: native Bastion access to the private AKS API through `aks-preview`**. This is the primary Kubernetes management path from beginner lab 3 through labs 10-15, 20-23 and the cumulative lab-24 extension; do not claim the whole curriculum is GA-only. Existing core labs source review: **2026-09-10**; Bastion integration checked **2026-10-01**; optional KAITO extension: **2026-09-14**; optional storage and confidential-compute extensions: **2026-09-23**. Beginner guides reuse that foundation and require runtime discovery of supported versions and capacity. Live deployment, regional capacity, end-to-end recovery, and Azure charges have **not** been verified in your subscription. Check the runtime prerequisites before applying anything.

The sample has **no end-user authentication by default**. The optional
[API authentication workshop](../workshops/api-authentication.md) enables it only in a declared
test boundary; it does not change primary deployments. Use synthetic data only. Workload identity
authenticates the application to Azure, not its customers. A real solution still needs
application authorization, abuse controls, data classification and its own threat model.


Lab 28 independently uses native Bastion with the same preview exception. Its overlap is intentional fault injection outside the supported address plan, not an exception permitting overlapping networks in the cumulative platform.

The baseline enables private AKS management immediately. ACR, Service Bus and Key Vault start with authenticated public data endpoints during bootstrap; lab 12 creates Private Link and disables those public endpoints. This deliberate intermediate state is **not the finished enterprise baseline**. Do not import business data or treat beginner completion or lab 10 alone as production-ready.

Lab 12 also establishes private ingress DNS and an explicitly approved certificate bundle on the Linux management host. Later Bash load exercises depend on that ordinary HTTPS path; the earlier one-off `curl --resolve --cacert` check alone does not satisfy it.

## Before spending anything

Use a dedicated subscription. Agree the subscription ID, primary and secondary regions, permitted billable footprint, and cleanup owner before running deployment commands. The supplied scripts do not deploy automatically when opened.

**Foundation** below means beginner lab 2 **or** a fresh experienced start at lab 10, not both. Beginner graduates reuse it. Lab 23 deploys a separate regional foundation.

| Resource | When introduced | Important cost/availability consideration |
|---|---|---|
| AKS Standard pricing tier, 3 system + 3 application nodes | Foundation | Default is six 4-vCPU VMs; allow node-family quota plus upgrade surge and scaling headroom. Zones and VM SKUs must exist in the region. |
| Azure Bastion Standard, two scale units + Standard public IP | Foundation / 23 | Included in each regional foundation deployment. Standing charges remain after disconnecting; the public IP belongs to Bastion, not AKS or a management VM. |
| Private Ubuntu build VM, Standard_B2s, 64-GiB Standard SSD | Foundation / 23 | Two burstable vCPUs and 4 GiB RAM, plus 2 GiB swap. No VM public IP. Check B-series quota/availability; sustained builds can exhaust CPU credits. Set `BuildVmSize` to an approved larger x64 SKU if needed. |
| ACR Premium, Service Bus Premium (one messaging unit), Key Vault | Foundation | Premium dependencies support the private network exercises; Service Bus can be a significant standing cost. |
| Log Analytics | Foundation | Ingestion and retention are billable; use synthetic low-volume traffic. |
| Azure Firewall Standard + public IP and hub peering | Foundation / 23 | Foundation supplies explicit build-VM egress; lab 12 transitions AKS node egress to it. Firewall standing charges dominate the small VM's cost and continue when the VM is deallocated. One frontend IP is a lab simplification, not production SNAT sizing guidance. |
| Private endpoints and optional separate CI runner | 12-13 | Private DNS/routing serves ACR, Key Vault, Service Bus, application HTTPS and PostgreSQL. Runner registration, isolation and any additional compute remain separately approved. |
| Managed Prometheus, Grafana, Application Insights | 14 | Query, ingestion, retention, and Grafana costs depend on configuration. |
| Extra nodes, NAP comparison, Spot | 15 | Hard-bound load, replica count, and node capacity. Stopping a NAP cluster is unsupported. |
| Defender, PostgreSQL, disk/file storage, backup | 20-21 | Protection, snapshots, storage, and retained backups can keep costing money after workloads stop. |
| Second region, Front Door/WAF and Fleet-related resources | 23 | Deploy only for the capstone and remove deliberately afterward. |
| KAITO GPU inference | 24 (optional) | One desired A100 GPU node; quota/capacity and explicit timed cleanup required. Workspace deletion does not delete GPU node pools. |
| RabbitMQ storage comparison | 25 (optional) | Dedicated AKS cluster with three storage-optimized nodes. Local NVMe is ephemeral; the optional Elastic SAN extension has a substantial minimum provisioned capacity. |
| Confidential compute | 26 (optional) | Dedicated AKS cluster with ordinary and AMD SEV-SNP confidential VM pools. CVM SKU quota, regional availability and idle confidential nodes drive cost. |
| Overlay overlap incident | 28 (optional) | Dedicated two-node AKS cluster, two private Ubuntu VMs/disks, NAT Gateway/public IP, Bastion Standard/two scale units/public IP and peering traffic. An intentional unsupported overlap is confined to this disposable boundary. |
| API private-connectivity cutover | 29 (optional) | Dedicated two-node AKS cluster, two private probe VMs/disks, two NAT Gateways/public IPs, PLS/PE, private DNS and traffic. No cumulative API changes. |

Create Azure Cost Management budgets and alerts for the subscription/resource groups before starting. **Budgets are notifications, not spending caps.** See [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) using your agreement and regions, and [budget guidance](https://learn.microsoft.com/azure/cost-management-billing/costs/tutorial-acm-create-budgets). No fixed currency estimate is supplied because region, agreement, duration, and traffic dominate it.

### Early go-no-go decisions

Discover these blockers before creating the foundation, even if the feature comes later.
If unavailable, choose a declared stopping point or an offline/design-only path; do not
leave an expensive foundation running while obtaining indefinite approvals.

| Stage | Approval or external prerequisite |
|---|---|
| 1-2 / fresh 10 | Dedicated subscription; resource deployment and role-assignment rights; existing administrator group; regional SKU, zones, versions and quota; separate SSH keys; cleanup owner. |
| 3 onward | Workstation native Bastion extension and approved preview exception; interactive Entra sign-in on the private build VM. |
| 13 | Existing private GitHub repository; a plan supporting the required protection/environment review controls; authorized reviewer; ephemeral private runner and its isolation/egress; runtime Git credential rotation owner. A solo practice waiver must be explicit, not described as independent approval. |
| 14 | Approved telemetry network/retention model, Grafana cost and an alert recipient. |
| 20 | Real team test identity without inherited admin rights; subscription owner approval for Defender pricing; asynchronous policy/scan time. |
| 21-22 | PostgreSQL/backup regional support and quota, restore retention/cleanup authority, supported upgrade target and approved window. |
| 23 | Distinct region and independent recovery access; two owned origin names and public-CA certificates; replication support and two-region budget. |
| 24-26 | Specialist GPU/storage/confidential capacity and explicit timed teardown. 25/26 need their own private API access design. |
| 28-29 | Dedicated disposable boundaries; 28 must never connect to shared networks. 29 alone authorizes a separate public-to-private API experiment. |
| Authentication workshop | Directory app-registration/consent authority and approved test client, independently of Azure resource roles. |

### Tooling and support

This is the central **curriculum baseline**, not a claim that every component's service minimum
is the same version. Lower minimums quoted by an upstream feature do not override it.

| Tool or surface | Reviewed baseline / rule |
|---|---|
| Shell and Python | Linux, Bash 5+, Python 3.12+; executable helpers also support Zsh 5.9+. Lab blocks remain Bash. |
| Azure CLI | 2.90+; inspect current command help and regional service support before execution. |
| Native Bastion extensions | `aks-preview` 22.0.0b5 and `bastion` 1.4.3, checked 2026-10-01; explicitly accepted preview exception. Do not silently downgrade an incompatible installed release. |
| Kubernetes client | Match the chosen supported AKS minor, or remain within supported one-minor skew. Record embedded Kustomize version with `kubectl version --client`. |
| TLS/database | OpenSSL 3+; PostgreSQL client 17+ for the lab-21 `sslrootcert=system` path. Container TLS retains its explicit system CA bundle. |
| Build/delivery | Rootless Podman with SBOM/digest support; Helm 3; same Flux CLI/controller version when mirroring and bootstrapping. |
| GitHub runner/actions | Private ephemeral Linux x64 publishing runner; version compatible with pinned Actions (at least 2.329.0 for Node 24 actions). Full-SHA Actions updates require review. |
| Specialist tools | Use each guide's explicit operator/chart/extension support gate; no inferred blanket GA exception. |

Version literals remain in executable bootstrap/workflow files where required for reproducibility.
When updating them, update this table and the affected lab's support note together. Record
**source review**, **offline validation**, and **live execution** separately; none substitutes
for another. Original core source review is 2026-09-10; the curriculum restructuring itself
does not recertify those service claims. Before execution review the linked live support
matrices, especially Gateway API, backup, Fleet, KAITO and storage.

## Tooling, access, and private connectivity

Use the [reviewed tooling baseline](#tooling-and-support) on Linux with GNU coreutils/findutils,
`jq`, `curl`, OpenSSH (`ssh-keygen`), `ca-certificates`, `dig` (dnsutils/bind-utils), Bicep,
Git, kubelogin, Helm, and Podman with Linux container build capability on the build machine.
The explicit Bastion preview exception does not authorize unrelated preview features.
Later guides identify additional role and extension prerequisites.

For the foundation bootstrap workstation, Bash, Git, `jq`, Azure CLI/Bicep and OpenSSH are sufficient; install the Bastion extension before SSH. The provisioned VM supplies the remaining lab tools. Local `kubectl`/kubelogin are needed only if you also use native AKS access directly from that workstation.

An Azure Contributor role alone cannot create RBAC assignments. The bootstrap operator needs resource deployment plus role-assignment authority at the relevant scopes, for example Contributor and Role Based Access Control Administrator in the dedicated subscription. Use an existing Entra security group you belong to for the lab cluster administrator group. Group creation requires separate directory rights; subscription ownership does not grant them. Later labs use separate identities with narrower permissions.

**The primary Kubernetes path is workstation -> Bastion -> private AKS API.** The foundation invokes `infra/bastion.bicep` as its `bastion` module after creating `AzureBastionSubnet`; it does not expose the API or require a jump VM for API access. The foundation what-if and apply confirmation cover Bastion's cost too. For an older existing foundation, lab 10 includes a focused add-on deployment; never replay a progressed foundation just to add Bastion. Native tunneling uses the explicitly approved preview CLI path with ordinary Entra user credentials. Local accounts stay disabled; never add `--admin`, disable TLS verification, or copy a public-endpoint workaround. A standard public Cloud Shell or hosted runner still has no direct private API/ACR connectivity.

### Bastion sessions and data-plane access

After foundation deployment in lab 2 or a fresh lab 10, activate a tunnel in the current shell from the repository root:

```bash
source ./scripts/activate-aks-bastion.sh
```

The sourceable helper starts the lower-level Bastion tunnel as a background child of the current Bash/Zsh session and exports a mode-600 temporary `KUBECONFIG` pointing to localhost while retaining the cluster CA and private FQDN as `tls-server-name`. Keep that shell alive. Do not run `az aks get-credentials` against the active tunnel kubeconfig: it would replace localhost with the unreachable private endpoint. Run `disconnect_aks_bastion` when finished; it stops the exact tunnel PID, removes its temporary files and restores the previous `KUBECONFIG`. A closed terminal normally terminates its child tunnel, but explicit disconnect is the required cleanup path.

For a second terminal on the **same host**, print `printf '%s\n' "$KUBECONFIG"` in the owner shell, then export that exact path in the second terminal. Run `kubectl config current-context` there before the requested port-forward/diagnostic command. Both terminals share the one tunnel; the exported path is unusable after `disconnect_aks_bastion`. Do not copy tunnel kubeconfigs to other hosts, and do not search unrelated processes to guess a port. The existing `bash ./scripts/connect-aks-bastion.sh` foreground/subshell workflow remains available and is useful when lab 23 needs independently owned regional tunnels with distinct ports and contexts.

**Bastion is not a VPN.** It does not route workstation `podman`, `curl`, `psql`, or Azure data-plane CLI calls into the VNet. Foundation therefore includes a private Ubuntu build/management VM in the spoke's management subnet. Access it through Bastion SSH, and run the mixed lab command blocks there with a native AKS API session. Ordinary lab-12 HTTPS, private registry builds, secret operations and SQL then use the VM's own VNet routing/DNS. No VPN or public IP on the VM is required.

**Full-curriculum path:** the build VM uses Azure DNS in the same spoke as AKS and the later service private DNS zone links. Its management subnet is private (`defaultOutboundAccess: false`) and routes internet traffic through the foundation firewall; private spoke traffic stays local. The firewall permits the reviewed package/build destinations only from the management subnet, without broadening node permissions. Bastion's subnet does not inherit this route table. ACR becomes private in lab 12 and PostgreSQL is private when introduced in lab 21; do not enable public endpoints to compensate for missing operator connectivity.

For a peered data-plane/management VNet, link the AKS private DNS zone and service private DNS zones to that VNet, or configure conditional forwarding through Azure DNS Private Resolver. Peering alone does not configure DNS. Do not expose TCP 3389/22 publicly to shortcut access. Build on a Podman-capable Linux host or workstation that can resolve and reach private ACR after lab 12. Keep the Bastion subnet out of the AKS node UDR; Bastion has its own documented routing/NSG requirements.

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

Do not redeploy foundation or regenerate manifests simply because you moved hosts; transfer the current local rendered/certificate state when continuing beginner labs 5-6 or intermediate labs 10-12. Use a Python virtual environment when installing application dependencies on Ubuntu. If viewing the lab-5/lab-10 browser UI from the workstation, open an additional Bastion SSH session with `-- -L 127.0.0.1:8080:127.0.0.1:8080`, then run the API port-forward on the VM.

The build VM is not automatically registered as a GitHub runner. Lab 13 still requires an approved ephemeral runner, its OIDC publisher identity, and a separate unprivileged runner account with rootless Podman; never run repository workflows as `labbuilder` with access to the operator's Azure/GitHub credentials. An isolated runner VM is preferable for customer workloads.

Build-host source review **2026-10-01**: [Azure cloud-init](https://learn.microsoft.com/azure/virtual-machines/linux/using-cloud-init), [Bastion native SSH](https://learn.microsoft.com/azure/bastion/connect-vm-native-client-linux), [explicit outbound networking](https://learn.microsoft.com/azure/virtual-network/ip-services/default-outbound-access), [Trusted Launch](https://learn.microsoft.com/azure/virtual-machines/trusted-launch), and [Podman build/SBOM options](https://docs.podman.io/en/v4.9.3/markdown/podman-build.1.html). Pinned tool assets/checksum locations and offline templates were checked; live provisioning and regional capacity remain unverified.

## Bootstrap configuration

From the repository root, create the ignored local configuration once. Do not overwrite an existing checkout's settings:

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

Edit the configuration before running the following block. Choose a private lab FQDN for `Hostname` (for example `orders.aks-lab.test`); lab 12 uses private DNS and a locally trusted self-signed certificate, so public domain ownership is not required. Lab 23 separately requires two owned origin names and public-CA certificates. Select an approved secondary region distinct from `Location` for lab 23. Discover a supported regional Kubernetes patch using the lab 10 preflight commands.

```bash
# Edit all REPLACE values, choose regions and a unique 4-12 character lowercase prefix.
settings=$(jq -e . ./local.settings.json)
az login --tenant "$(jq -er .TenantId <<<"$settings")"
az account set --subscription "$(jq -er .SubscriptionId <<<"$settings")"
az aks get-versions --location "$(jq -er .Location <<<"$settings")" -o table
az vm list-usage --location "$(jq -er .Location <<<"$settings")" -o table
az vm list-skus --location "$(jq -er .Location <<<"$settings")" --size "$(jq -er .VmSize <<<"$settings")" --all -o table
az vm list-skus --location "$(jq -er .Location <<<"$settings")" --size "$(jq -er '.BuildVmSize // "Standard_B2s"' <<<"$settings")" --all -o table
# Select a supported GA patch with a supported upgrade path for lab 22; save it in settings.
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

All `.sh` helpers under `scripts/`, `ops/` and `advanced/` support execution with Bash 5+ or Zsh 5.9+. The shebang defaults to Bash; choose Zsh explicitly with `zsh ./scripts/<name>.sh --kebab-case-option value`. Execute helpers rather than sourcing them, except for `scripts/use-lab.sh`, `scripts/activate-aks-bastion.sh` and the shared `scripts/lib.sh`. The lab command blocks remain Bash-oriented; run `bash` before following those blocks if your terminal uses another shell. Offline helper tests run under both shells when Zsh is installed.

`use-lab.sh` verifies your settings and Azure context; it never silently changes subscriptions or shell options. Source it **from Bash or Zsh** to load `Lab` and `Outputs` as JSON strings. Sourcing does not honor the script's shebang, and running `bash ./scripts/use-lab.sh` or `zsh ./scripts/use-lab.sh` does not load variables into your current shell. A validation error returns a nonzero status so you can correct `local.settings.json` and retry; if you already enabled `set -e`, use `if source ./scripts/use-lab.sh; then echo 'Lab state loaded'; else echo 'Fix the reported error before continuing' >&2; fi` to keep the shell open. Read settings with `lab_value Prefix` and deployment outputs with `output_value registryServer`; use `jq` for other JSON responses. Execute other helpers with `bash ./scripts/<name>.sh --kebab-case-option value` or the equivalent `zsh` command; script filenames use lowercase kebab-case. The helpers obtain generated resource names from the `foundation` deployment. Do not commit local settings, credentials, rendered certificates or evidence containing personal data.

The `aks` RSA key supplies the supported node Linux profile; its passphrase-protected private key is for controlled break-glass access only. The distinct `build-vm` key authenticates Bastion SSH to the build host. Neither authenticates applications, and neither VM nor nodes have a public SSH endpoint. Disabling local Kubernetes administrator credentials is separate from node SSH configuration. Do not enable preview SSH features simply to remove this bootstrap input.

## Repository layout and ownership

- `infra/`: initial foundation (Bastion, private build VM/bootstrap, firewall hub/peering), and focused network deployments.
- `app/`: synthetic order API/frontend and worker in one Python image; dependencies pinned in `requirements.txt`.
- `k8s/`: templates for the initial workload, identity mount, and private ingress/network policies.
- `scripts/`: context, foundation what-if/deployment, native Bastion connection, manifest rendering and lab certificate generation.
- `ops/`, `.github/workflows/`, `gitops/`: delivery, monitoring and scaling assets introduced by labs 13-15.
- `advanced/`: governance, data, upgrade and regional recovery assets introduced by labs 20-23, plus the optional lab-24 KAITO manifests.
- `labs/01-*` through `labs/06-*`: guided beginner operations using the shared foundation and helpers.
- `labs/25-*` through `labs/29-*`: self-contained specialist guides; their generated files live in disposable local working folders and are not dependencies of the cumulative environment. Lab 27 is offline; lab 28 uses its own private cluster/Bastion and deliberately conflicting peer network. Lab 29 uses a separate VNet-integrated cluster and unpeered private-endpoint consumer.
- `rendered/`, `evidence/`, `.artifacts/`: local generated output, excluded from Git.
- `docs/`: this shared operating handbook and the stage/ownership map.
- `workshops/`: optional focused exercises; each declares offline, isolated-namespace or test-only scope.
- `tests/` and [CONTRIBUTING](../CONTRIBUTING.md): credential-free regression checks, not live deployment certification.

**Ownership changes are explicit.** Beginner labs 5-6 and intermediate labs 10-12 use imperative commands and rendered templates to expose the mechanics. Lab 13 adopts the workload into GitOps. After adoption, change the Git source or explicitly suspend/resume the named Flux reconciliation for a fault exercise. Platform infrastructure stays outside the application's Flux permissions.

`infra/main.bicep` is the **initial bootstrap snapshot**, not a continuously reconciled production platform module. Later labs intentionally change outbound type, public-network flags, add-ons, autoscaling, and versions through focused commands. **Do not redeploy the initial foundation over a progressed cluster:** that would request old values and can overwrite network/subnet configuration. For a production implementation, fold the final selected settings into owned infrastructure modules and review what-if before every application. A second-region bootstrap is a new resource group, not a replay against the progressed primary.

## Ordinary HTTPS sessions

After lab 12 has created and verified the private DNS record and certificate, run this in
each new **private management-host** terminal. It only restores explicit client trust;
it does not create DNS, renew a certificate or supply network connectivity.

```bash
source ./scripts/use-lab.sh
test -r "$Root/rendered/certs/lab-ca-bundle.pem"
openssl x509 -in "$Root/rendered/certs/tls.crt" -noout \
  -checkhost "$(lab_value Hostname)" -checkend 0
export CURL_CA_BUNDLE="$Root/rendered/certs/lab-ca-bundle.pem"
export SSL_CERT_FILE="$CURL_CA_BUNDLE"
export REQUESTS_CA_BUNDLE="$CURL_CA_BUNDLE"
curl --fail --show-error --max-time 10 "https://$(lab_value Hostname)/readyz"
```

If validation fails, inspect DNS, certificate expiry and the saved fingerprint using lab 12.
Do not add a supplied arbitrary root or bypass validation. The local lab certificate lasts
14 days; use lab 12's renewal exercise if expired. If using a separately approved enterprise
certificate, use that client's approved CA path instead. Lab 23's origin certificates and
edge hostname are separate from this internal hostname.

## Reviewed Git changes

Lab 13 teaches branch, commit, PR, independent review, merge and reconcile manually once.
Thereafter use the same workflow through `ops/publish-reviewed-change.sh`. The old
`advanced/publish-reviewed-change.sh` entry point remains a compatibility wrapper.

Start on up-to-date `main`, inspect the diff, and stage **only the paths named by your task**.
The helper refuses stale `main`, empty staging and closed/unmerged PRs. It does not approve
or merge its own PR, and does not run Flux. Private operator credentials must never be
available to an untrusted workflow.

```bash
git status --short
git diff
# Stage the task's exact files first; do not use git add . for a lab checkout.
git diff --cached --stat
bash ./ops/publish-reviewed-change.sh --message "Describe the reviewed lab change"
```

Only after it returns successfully, reconcile the **task's exact named owner** and verify
the applied revision, rollout and business evidence. Platform changes can have different
owners from `orders`; see the [ownership map](state.md). If stopped with a pending PR,
complete or abandon that review deliberately and return to updated `main`. Do not rerun a
mutation while pretending the previous review completed.

For an emergency fault: record the before-state, suspend only the named child, run the bounded
injection, explicitly remove new live fields not owned by Git, resume/reconcile, and verify
recovery. An EXIT trap is best effort; forced process termination or host loss requires the
lab's explicit recovery commands on re-entry. Never leave Flux suspended as a final repair.

## Evidence and completion

Use a private worksheet or ignored `evidence/` directory. A checkpoint records UTC time,
lab/part, host and cluster identity, Git SHA/image digest where applicable, original state,
observed outcome, remaining resources, unresolved blockers and the next safe action.
Never capture tokens, raw kubeconfigs, private keys or real customer data. Export only sanitized
evidence if sharing a report.

| Completion label | Required evidence |
|---|---|
| Hands-on complete | Actual allowed/denied behavior, recovered health and task exit conditions; operation submission alone is insufficient. |
| Offline complete | Render, local tests or synthetic evidence analysis, with no live Azure claim. |
| Design-only complete | Architecture/trade-off decision, named permission and cost boundary, proposed checks and rollback/cleanup; live tasks explicitly not executed. |
| Blocked / inconclusive | Missing support, quota, telemetry, unavailable upgrade, failed recovery or ambiguous fault evidence; state the blocker. |

Keep challenge, required evidence and stop conditions visible; worked solutions can collapse.
Each lab's estimated active time excludes stated provider waits and approval delays. A waiting
period is a checkpoint, not justification for relaxing a safety gate.

For independent specialist labs, use their exact isolated subscription/group and disposable
working directory. Do not reuse cumulative kubeconfigs or infer ownership from a name alone.
Retain evidence outside a disposable directory before deleting it. Keep each guide's
resource-specific interruption and cleanup checks even when using this shared contract.

### Duplicate and restart evidence

This is one experiment applied twice, not two claims of exactly-once delivery. Use the
bounded executable procedure in [lab 15 task 8](../labs/15-scaling.md#8-prove-duplicate-handling-restart-limitations-retry-and-dead-letter-paths)
and the durable extension in [lab 21 task 3](../labs/21-data-recovery.md#3-bind-identities-and-enable-durable-application-behavior-through-git).

| Step | Before lab 21 | After lab 21 |
|---|---|---|
| Submit one unique ID/item twice to one current worker | First processed, second duplicate; `durable:false`. | One stored row, second duplicate; `durable:true`. |
| Restart that worker, then submit the identical ID/item | Memory lost: processed again, explicitly a durability failure. | Duplicate after restart; matching GET and SQL count exactly one. |
| Reuse ID with a different item | Same-process detection only; do not generalize across restarts. | Specific `InvalidOrder` dead-letter evidence and unchanged original row. |
| Finish | Remove KEDA pause and restore Flux; keep known dead-letter evidence. | Same cleanup; record the new dead-letter baseline for DR. |

HTTP 202 is queue acceptance, not completion. A queue count alone cannot attribute a message.
An interrupted receive is inconclusive without old and replacement worker evidence. SQL
uniqueness does not atomically cover unrelated side effects; external payments need their own
idempotency/outbox design.

## Application behavior worth understanding

`POST /orders` returns 202 after Service Bus accepts a message, not after processing commits. The worker uses token authentication over AMQP WebSockets (TCP 443). It completes the message only after its processing step succeeds.

Before lab 21, deduplication is only in memory **per worker process** and disappears on restart; multiple replicas can both process a repeated ID. Do not infer durable exactly-once processing. Lab 21 adds a PostgreSQL primary key and transaction before message completion. This supports idempotent database effects, but does not magically make arbitrary external side effects exactly-once. Reusing an ID with a different item is rejected to the dead-letter queue.

`GET /orders/{id}` intentionally returns 503 before lab 21, then queries durable state. `/healthz` and `/readyz` are process/readiness signals, not deep dependency probes. `/config-version` exposes only the deliberately synthetic CSI `lab-version` value; never map a real secret there.

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

Save evidence of each lab's exit criteria and Git commit once GitOps exists. Close temporary port-forwards and remove fault injections before pausing, then run `disconnect_aks_bastion`. Resume with `source ./scripts/activate-aks-bastion.sh`, verify the current cluster context, and read the next lab's prerequisites rather than rerunning bootstrap commands. Lab 23 can use the foreground `connect-aks-bastion.sh` workflow for independently owned regional tunnels and merged kubeconfig paths. Disconnecting stops only the local tunnel; Bastion and its public IP continue billing.

To pause build-VM compute, first close API/SSH sessions and any runner, then run `az vm deallocate --ids "$(output_value buildVmId)"` from the original workstation after sourcing `use-lab.sh`. Use `az vm start --ids "$(output_value buildVmId)"` before reconnecting. Guest shutdown alone is not Azure deallocation. The disk, firewall and Bastion still cost money; finish VM-dependent cleanup and retain evidence before final resource-group deletion.

Do not delete cumulative resources after individual labs. If you ran lab 24, complete its Workspace and GPU-pool cleanup before pausing or final teardown. Labs 25 and 26 use their own resource groups and private management prerequisites, not the cumulative Bastion sessions; complete each guide's teardown instead of leaving specialist node pools or storage resources running. Lab 27 is local and disposable. Lab 28 has its own tagged group and native Bastion session; complete its confirmed teardown, including its NAT/Bastion and private VMs, even if the collision exercise failed partway. Lab 29 has a separate tagged group; delete its cluster, probe VMs, NAT Gateways, PE/PLS and DNS even if cutover failed. At final teardown, follow lab 23 and lab 21 cleanup first **if those stages were reached**: remove Fleet membership and global routing, stop/delete backup protection using the documented retention process, remove diagnostic/monitoring associations and external role assignments, and handle database/volume backups intentionally. For beginner-only environments, use the next section instead. Keep Bastion available until API-dependent cleanup is complete, then disconnect all current-shell tunnels and close any foreground tunnel-owner subshells before deleting their Bastion hosts/public IPs. Inspect every resource group before deletion, from the original workstation rather than the VM being deleted:

```bash
source ./scripts/use-lab.sh
az resource list --resource-group "$(lab_value ResourceGroup)" -o table
# Only after confirming this is the dedicated disposable group and retaining required evidence:
az group delete --name "$(lab_value ResourceGroup)"
```

The Azure command prompts for confirmation. Repeat only for explicitly identified secondary/comparison groups, never for a shared subscription or wildcard set. Key Vault purge protection deliberately prevents immediate purge/name reuse. Retained backup data, registries, snapshots, Grafana, public IPs, DNS zones, runners, and resources outside the primary group need their own cleanup. Check Cost Management and Resource Graph afterward; deleting Kubernetes namespaces is not Azure teardown.

### Beginner-only teardown

Use this only when **permanently ending** after labs 1-6 or the unchanged lab-10 baseline. Continuing learners retain everything. If later labs were reached, use their resource-specific cleanup instead.

Save the intended evidence and identify the dedicated group and AKS-managed node group before deleting anything. Repair any injected selector, finish API-dependent inspection, log out Podman if you published an image, stop port-forwards and transfer tunnels, and run `disconnect_aks_bastion`. Close SSH sessions and return to the original workstation. Do not deallocate/delete the build VM while still depending on its kubeconfig or unsaved evidence.

On that workstation, use the resource-list and confirmed `az group delete` block above for the **exact dedicated foundation group**. This discards the synthetic workload and foundation; it is not a pause. AKS owns deletion of its managed node resource group; verify completion rather than deleting its VM scale sets independently. If initial provisioning failed, inventory partially created resources too.

No Fleet, PostgreSQL, backup vault, GPU pool or GitOps teardown is required unless you separately created those resources. Inspect for out-of-group resources/role assignments and retained data, respect Key Vault purge protection, and check Cost Management afterward. Only then retire the specific local configuration, keys and generated artifacts you no longer need; never delete an entire checkout as a cleanup shortcut.
