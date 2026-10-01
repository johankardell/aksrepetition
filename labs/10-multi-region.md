# Lab 10 — Recover the business service across regions, then fail back

**Customer:** a regional failure must not become an unbounded data-integrity incident. **Time:** one or two days, including replication/provisioning. **Cost:** two AKS platforms/firewalls, replicated Service Bus Premium, two PostgreSQL servers, Front Door Premium and WAF, ACR replication, private endpoints and traffic. Obtain an explicit budget/change approval before execution.

This capstone depends on labs 1–9. All commands run from the workspace root in Linux Bash with `jq`, `curl`, `getent`, and `psql`. Use a VNet-connected management host **in each region** or approved independently resilient private connectivity/DNS. Do not use a primary-region VM as the only recovery control point. No public AKS API, admin credentials, public database, or connection strings are introduced.

The Bash helpers use lowercase kebab-case filenames ending in `.sh` and accept `--kebab-case` options (for example `--registry-name`, `--host-name`, and `--base-uri`). Run helpers as `bash ./...sh`; executable permission bits are not required. `source ./scripts/use-lab.sh` supplies JSON strings `Lab` and `Outputs`, absolute repository path `Root`, and the strict `jq -er` accessors `lab_value KEY` and `output_value KEY`. Keep these shells open between tasks; on a second regional host, source the same primary settings and load the reviewed regional inventory/evidence separately. Do not enable shell tracing or record transcripts while handling tokens or private keys.

## 1. Sign the recovery contract before provisioning

**Challenge:** Agree on recovery authority, RTO/RPO, component-level recovery mechanisms and residual risks. Record the primary/secondary resource inventory and obtain the signed exercise contract before provisioning.

Define roles: incident commander authorizes promotion; platform operator fences traffic/compute; database owner authorizes PostgreSQL promotion; messaging owner authorizes namespace promotion; app owner validates accepted order IDs; scribe records UTC evidence. In this synthetic lab one operator may fill all roles explicitly.

Adopt these **lab targets**, not Azure guarantees: RTO ≤30 minutes after declaration; planned exercise RPO zero for the recorded accepted IDs. For an actual inaccessible-region event, asynchronous database replication can lose recent rows and a forced messaging promotion can lose messages or acknowledgements. Record actual lag and reconcile both systems. PostgreSQL and Service Bus do **not** share a distributed commit.

Requirements: GA regional AKS version, PostgreSQL General Purpose replica support and quota, Service Bus non-partitioned Premium geo-replication, Front Door Private Link origin region support, x86 Linux, valid public-CA TLS certificates for two owned origin names. A self-signed lab-3 certificate **cannot** be used with Front Door Private Link certificate validation. Use DNS-01 issuance through your approved CA process; do not open an origin just for certificate validation.

Keep extra regional values separate: do not overwrite primary `local.settings.json`. The secondary foundation prefix must be unique and 4–12 characters.

<details>
<summary>Solution</summary>

| Component | Recovery mechanism | What it does not do |
|---|---|---|
| Kubernetes configuration | Regional GitOps overlay at reviewed SHA | Does not restore orders or PVC contents |
| Application image | ACR Premium geo-replication; immutable digest | A tag alone does not prove identical content |
| Processed orders | PostgreSQL cross-region read replica, deliberate promotion | Async replication is not zero-loss regional HA |
| Pending orders | Non-partitioned Service Bus Premium **Geo-Replication** | Metadata-only Geo-DR does not copy messages |
| HTTP routing | Front Door Premium health probes + operator enable gate | Health probes do not promote data or fence writers |
| TLS/config/secrets | Regional Key Vault and independently issued origin TLS | Key Vault recovery is not arbitrary instant cross-region cloning |
| CSI exercise data | Lab-8 backup boundary | Local operational snapshots/private Files are not region DR |
| Cluster updates | Fleet staged update runs | **Fleet does not perform application/data disaster recovery** |

Record all extra values separately; do not overwrite `local.settings.json` for the primary:

```bash
set -euo pipefail
umask 077
source ./scripts/use-lab.sh
mkdir -p "$Root/.artifacts/advanced"
Primary=$(az deployment group show -g "$(lab_value ResourceGroup)" -n foundation --query properties.outputs -o json)
SecondaryRg="$(lab_value ResourceGroup)-secondary"
SecondaryPrefix="$(lab_value Prefix)dr" # Must remain <=12 characters for foundation.
if (( ${#SecondaryPrefix} < 4 || ${#SecondaryPrefix} > 12 )); then
  printf 'Choose a unique 4-12 character SecondaryPrefix.\n' >&2
  exit 1
fi
SecondaryCluster="$SecondaryPrefix-aks"
Pg="$(lab_value Prefix)-pg"
PgReplica="$(lab_value Prefix)-pg-dr"
FrontDoor="$(lab_value Prefix)-afd"
Fleet="$(lab_value Prefix)-fleet"
FluxNamespace='flux-system'
# Use the exact primary app/source names recorded in labs 4/7.
AppKustomization='orders'
GitSource='flux-system'
PrimaryContext="$(lab_value ClusterName)-primary"
SecondaryContext="$SecondaryCluster-secondary"
az aks get-credentials -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --context "$PrimaryContext" --overwrite-existing
```

The signed contract names the approving person for each role, declaration time, accepted-ID evidence source, promotion/fencing gates, success criteria and abort authority. Measure RTO from declaration to verified business service, while recording the later time when all accepted work has completed. Evaluate the planned zero-loss target by matching every accepted ID **and item**, not by assuming two replication dashboards jointly prove consistency. A missing record or unknown lag is an unresolved outcome, not an inferred zero.

</details>

## 2. Reproduce the second platform from the same foundation

**Challenge:** Reproduce a private secondary AKS platform with non-overlapping addressing, regional management access and controlled egress. Retain rendered network ranges, version/SKU checks, node health and observability/guardrail coverage.

Select a supported version available in **both** regions, compatible with lab 9, and confirm zones/SKU availability.

The current `infra/main.bicep` exposes `networkPrefix` as the first two address octets. This lab uses `10.60` for the secondary spoke and `10.70` for its firewall hub; primary remains `10.40`/`10.50`. Do not replace this with an invented parameter name or reuse overlapping ranges. If the foundation parameter is renamed later, update the invocation and validate the rendered subnet ranges before deployment.

Configure regional management connectivity/private DNS before accessing the secondary API. Associate the **secondary** route table/subnet and transition its outbound type; never change the primary by accidentally using its outputs.

The orders DR workload uses the primary geo-replicated ACR and Service Bus, not the unrelated secondary foundation registry/namespace. Those unused resources still cost money; retain them until validation and delete only after proving no consumers. Apply labs 5/7 observability/platform guardrails through their parameterized deployment/Git paths, record omissions, and verify both clusters' supported add-ons before Fleet updates.

<details>
<summary>Solution</summary>

```bash
az aks get-versions -l "$(lab_value SecondaryLocation)" -o table
az vm list-usage -l "$(lab_value SecondaryLocation)" -o table
az vm list-skus -l "$(lab_value SecondaryLocation)" --size "$(lab_value VmSize)" --all -o table
az postgres flexible-server list-skus -l "$(lab_value SecondaryLocation)" -o table
Cluster=$(az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" -o json)
KubernetesVersion=$(jq -er '.kubernetesVersion' <<< "$Cluster")
AdminGroupId=$(jq -er '.aadProfile.adminGroupObjectIDs[0]' <<< "$Cluster")
PublicKeyPath='./rendered/keys/aks.pub'
SshPublicKey=$(cat "$PublicKeyPath")
az group create -n "$SecondaryRg" -l "$(lab_value SecondaryLocation)"
az deployment group create -g "$SecondaryRg" -n foundation -f ./infra/main.bicep \
  -p "prefix=$SecondaryPrefix" "location=$(lab_value SecondaryLocation)" "kubernetesVersion=$KubernetesVersion" \
  "adminGroupObjectId=$AdminGroupId" "sshPublicKey=$SshPublicKey" "vmSize=$(lab_value VmSize)" networkPrefix=10.60
Secondary=$(az deployment group show -g "$SecondaryRg" -n foundation --query properties.outputs -o json)
printf '%s\n' "$Secondary" > ./.artifacts/advanced/secondary-outputs.json
az deployment group create -g "$SecondaryRg" -n private-endpoints -f ./infra/private-endpoints.bicep \
  -p "prefix=$SecondaryPrefix" "location=$(lab_value SecondaryLocation)" "vnetId=$(jq -er '.vnetId.value' <<< "$Secondary")" \
  "subnetId=$(jq -er '.endpointsSubnetId.value' <<< "$Secondary")" "acrId=$(jq -er '.acrId.value' <<< "$Secondary")" \
  "keyVaultId=$(jq -er '.keyVaultId.value' <<< "$Secondary")" "serviceBusId=$(jq -er '.serviceBusId.value' <<< "$Secondary")"
az deployment group create -g "$SecondaryRg" -n egress -f ./infra/firewall.bicep \
  -p "prefix=$SecondaryPrefix" "location=$(lab_value SecondaryLocation)" "spokeName=$SecondaryPrefix-vnet" \
  spokeAddress=10.60.0.0/16 nodesAddress=10.60.0.0/20 hubPrefix=10.70
```

Repeat the **route-table association and AKS outbound-type transition** from lab 3 with secondary outputs, not the primary VNet. Inspect the firewall deployment output and apply the precise node subnet route association:

```bash
SecondaryEgress=$(az deployment group show -g "$SecondaryRg" -n egress --query properties.outputs -o json)
az role assignment create --assignee-object-id "$(jq -er '.clusterPrincipalId.value' <<< "$Secondary")" \
  --assignee-principal-type ServicePrincipal --role 'Network Contributor' --scope "$(jq -er '.routeTableId.value' <<< "$SecondaryEgress")"
az network vnet subnet update --ids "$(jq -er '.nodesSubnetId.value' <<< "$Secondary")" --route-table "$(jq -er '.routeTableId.value' <<< "$SecondaryEgress")"
az aks update -g "$SecondaryRg" -n "$SecondaryCluster" --outbound-type userDefinedRouting
az acr update -n "$(jq -er '.acrName.value' <<< "$Secondary")" --public-network-enabled false
az keyvault update -n "$(jq -er '.keyVaultName.value' <<< "$Secondary")" --public-network-access Disabled
az servicebus namespace update -g "$SecondaryRg" -n "$(jq -er '.serviceBusName.value' <<< "$Secondary")" --public-network-access Disabled
az aks get-credentials -g "$SecondaryRg" -n "$SecondaryCluster" --context "$SecondaryContext" --overwrite-existing
kubectl --context "$SecondaryContext" get nodes -o wide
```

Configure regional management connectivity/private DNS before the final command. Secondary foundation creates a separate ACR, Key Vault and Service Bus for reproducibility. **The orders DR workload will instead use the primary geo-replicated ACR and primary geo-replicated Service Bus namespace.** The unused secondary namespace/registry incur costs; retain until the exercise is validated, then delete only after proving no consumers reference them. Never treat two unrelated Service Bus namespaces as replicas.

Apply labs 5/7 observability and platform guardrails in the secondary through their parameterized deployment/Git paths, pointing telemetry at approved regional or central sinks. Record any omitted sensor/add-on as an explicit coverage gap. Fleet updates need supported add-ons in both regions, not merely two Ready clusters.

</details>

## 3. Make images, identities and secrets survive primary loss

**Challenge:** Make the approved image digest, least-privilege regional identities and required secrets usable without a primary-region PE. Prove a secondary-local DNS path and actual digest pull; identify how regional certificates/secrets will be recovered.

Use each cluster's own issuer and regional identities. Scope shared Service Bus sender/receiver grants to the orders queue. Link regional Service Bus private DNS zones only to their own VNets: two same-name PE records in a shared zone can route clients to a failed region. Reinspect **both** ACR PE DNS-zone groups after replication, including regional data records.

Keep secrets and TLS keys out of Git. Use the secondary vault's PE and scoped RBAC; rehearse supported backup/restore, replication or regeneration under its geo/tenant constraints. If the chosen image needs the lab-2 sample secret, reproduce its regional SecretProviderClass/mount **before admitting traffic**; the base DR app does not mount it.

<details>
<summary>Solution</summary>

```bash
az acr replication create --registry "$(lab_value AcrName)" --location "$(lab_value SecondaryLocation)"
az acr replication list --registry "$(lab_value AcrName)" -o table
SecondaryKubelet=$(az aks show -g "$SecondaryRg" -n "$SecondaryCluster" --query identityProfile.kubeletidentity.objectId -o tsv)
az role assignment create --assignee-object-id "$SecondaryKubelet" --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope "$(jq -er '.acrId.value' <<< "$Primary")"
bash ./advanced/new-private-endpoint.sh --resource-group "$SecondaryRg" --location "$(lab_value SecondaryLocation)" \
  --name "$SecondaryPrefix-shared-acr-pe" --resource-id "$(jq -er '.acrId.value' <<< "$Primary")" --group-id registry \
  --subnet-id "$(jq -er '.endpointsSubnetId.value' <<< "$Secondary")" --vnet-id "$(jq -er '.vnetId.value' <<< "$Secondary")" --zone-name privatelink.azurecr.io
```

ACR geo-replication creates regional data endpoints. Reinspect **both** ACR private endpoint DNS-zone groups after creating the replica; verify registry and secondary-region data FQDNs resolve through the local PE, and ensure secondary nodes pull the exact digest without crossing a primary PE. A private DNS zone group must include all required ACR records. Do not accept "ACR replication Succeeded" as an image-pull test.

```bash
ApprovedImage=$(kubectl --context "$PrimaryContext" get deployment order-api -n orders -o jsonpath='{.spec.template.spec.containers[0].image}')
ApprovedWorker=$(kubectl --context "$PrimaryContext" get deployment order-worker -n orders -o jsonpath='{.spec.template.spec.containers[0].image}')
if [[ "$ApprovedImage" != "$ApprovedWorker" || "$ApprovedImage" != "$(lab_value RegistryServer)/order-app@sha256:"* ]]; then
  printf 'Primary API and worker must use the same approved registry digest from lab 4.\n' >&2
  exit 1
fi
ImageDigest="${ApprovedImage##*@}"
[[ "$ImageDigest" =~ ^sha256:[a-f0-9]{64}$ ]] ||
  { printf 'Resolve the approved app digest from the private registry first.\n' >&2; exit 1; }
az acr repository show -n "$(lab_value AcrName)" --image "order-app@$ImageDigest" --query digest -o tsv
BusId=$(jq -er '.serviceBusId.value' <<< "$Primary")
az role assignment create --assignee-object-id "$(jq -er '.apiPrincipalId.value' <<< "$Secondary")" --assignee-principal-type ServicePrincipal --role 'Azure Service Bus Data Sender' --scope "$BusId/queues/orders"
az role assignment create --assignee-object-id "$(jq -er '.workerPrincipalId.value' <<< "$Secondary")" --assignee-principal-type ServicePrincipal --role 'Azure Service Bus Data Receiver' --scope "$BusId/queues/orders"
bash ./advanced/new-private-endpoint.sh --resource-group "$SecondaryRg" --location "$(lab_value SecondaryLocation)" \
  --name "$SecondaryPrefix-shared-bus-pe" --resource-id "$BusId" --group-id namespace \
  --subnet-id "$(jq -er '.endpointsSubnetId.value' <<< "$Secondary")" --vnet-id "$(jq -er '.vnetId.value' <<< "$Secondary")" --zone-name privatelink.servicebus.windows.net
```

Each cluster uses its own issuer and regional user-assigned identities from foundation; do not reuse a primary issuer federation blindly. Regional Service Bus private DNS zones link only their own VNets; a shared zone with two same-name endpoint records can strand clients on a failed region.

Regenerate synthetic lab configuration in the secondary Key Vault from its authoritative source, using that vault's regional PE and access policy/RBAC. For real secrets, decide and rehearse supported backup/restore, replication or regeneration under the vault's geo/tenant constraints. Workload IDs remove Service Bus/PostgreSQL passwords; they do not remove TLS private keys. The base DR app does not mount the lab-2 sample secret; if your current image requires it, reproduce the SecretProviderClass/mount against the secondary vault **before admitting traffic**.

For the recovery decision, classify each value: non-secret configuration comes from reviewed Git/authoritative configuration, the synthetic sample can be regenerated, and a TLS key needs controlled regional distribution or independently issued regional replacement. A Workload ID grant replaces a password but not a certificate lifecycle. Record the secondary vault owner, rotation method and retrieval test rather than claiming that a deployed vault contains the primary's secrets.

Keep the application passive while preparing these dependencies. The actual secondary application pull is verified during the gated task-9 activation: inspect pod `imageID` and pull events there against `$ImageDigest`. Until that succeeds through local registry/data endpoint resolution, mark the pull evidence pending rather than calling replication status a completed image test.

</details>

## 4. Establish actual message and database replication

**Challenge:** Configure supported data-bearing message replication and a private PostgreSQL read replica. Prove secondary readiness, known-row arrival, SQL identity mapping and timestamped replication lag before any promotion.

Required path is **non-partitioned Premium** (one messaging partition). If the current namespace is partitioned, stop and migrate the synthetic exercise with the same identity/network controls; the partitioned preview path is not GA. Do not combine Geo-Replication with metadata-only Geo-DR. Synchronous replication is required for this planned queue exercise, with explicit latency/availability trade-offs; wait for secondary **Ready**.

Initialize secondary SQL identities on the primary **before** creating the replica; do not overwrite existing role mappings. Keep public database access disabled and validate TLS and the independent Entra administrator settings. PostgreSQL remains asynchronous. Missing lag metrics mean **unknown**, not zero; compare a known lab-8 row as well as dashboards.

<details>
<summary>Solution</summary>

Inspect current namespace partitioning first:

```bash
az servicebus namespace show -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" \
  --query '{sku:sku,partitions:premiumMessagingPartitions,replication:geoDataReplication}' -o json
```

Required path is **non-partitioned Premium** (one messaging partition). Partitioned namespace geo-replication is still preview in the current docs. If this namespace is partitioned, stop and migrate the synthetic exercise to a supported non-partitioned Premium namespace with the same identity/network controls; do not call the preview path GA.

```bash
jq -n --arg primary "$(lab_value Location)" --arg secondary "$(lab_value SecondaryLocation)" '[
  {"location-name": $primary, "role-type": "Primary"},
  {"location-name": $secondary, "role-type": "Secondary"}
]' > ./.artifacts/advanced/bus-locations.json
az servicebus namespace update -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" \
  --locations '@./.artifacts/advanced/bus-locations.json' --max-replication-lag-duration-in-seconds 0
az servicebus namespace show -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" --query geoDataReplication -o json
```

`0` selects synchronous replication for this zero-loss **planned** queue exercise; assess cross-region publish latency and availability trade-offs. Wait for secondary **Ready**. Geo-Replication copies message data/state; `georecovery-alias` / metadata-only Geo-DR does not. Do not configure both features on the same namespace.

Map secondary application identities into PostgreSQL **before** creating the replica so SQL roles are replicated:

```bash
Admin=$(az ad signed-in-user show -o json)
PgPrimary=$(az postgres flexible-server show -g "$(lab_value ResourceGroup)" -n "$Pg" -o json)
bash ./advanced/initialize-orders-database.sh --host-name "$(jq -er '.fullyQualifiedDomainName' <<< "$PgPrimary")" --admin-login "$(jq -er '.userPrincipalName' <<< "$Admin")" \
  --api-principal-id "$(jq -er '.apiPrincipalId.value' <<< "$Secondary")" --worker-principal-id "$(jq -er '.workerPrincipalId.value' <<< "$Secondary")" \
  --api-role orders_api_dr --worker-role orders_worker_dr
az postgres flexible-server replica create -g "$SecondaryRg" -n "$PgReplica" --source-server "$(jq -er '.id' <<< "$PgPrimary")" --location "$(lab_value SecondaryLocation)"
Replica=$(az postgres flexible-server show -g "$SecondaryRg" -n "$PgReplica" -o json)
az postgres flexible-server update -g "$SecondaryRg" -n "$PgReplica" --public-access Disabled
bash ./advanced/new-private-endpoint.sh --resource-group "$SecondaryRg" --location "$(lab_value SecondaryLocation)" \
  --name "$SecondaryPrefix-pg-pe" --resource-id "$(jq -er '.id' <<< "$Replica")" --group-id postgresqlServer \
  --subnet-id "$(jq -er '.endpointsSubnetId.value' <<< "$Secondary")" --vnet-id "$(jq -er '.vnetId.value' <<< "$Secondary")" --zone-name privatelink.postgres.database.azure.com
az postgres flexible-server microsoft-entra-admin create -g "$SecondaryRg" -s "$PgReplica" --object-id "$(jq -er '.id' <<< "$Admin")" --display-name "$(jq -er '.userPrincipalName' <<< "$Admin")" --type User
```

Verify replica health, replication lag and known lab-8 rows over TLS from the secondary management host. Use the lab-8 `psql` token environment; `SELECT pg_is_in_recovery();` must be true before promotion. The server's Entra administrator/control-plane settings are checked independently from replicated SQL roles.

```bash
az monitor metrics list-definitions --resource "$(jq -er '.id' <<< "$PgPrimary")" -o table
az monitor metrics list-definitions --resource "$BusId" -o table
```

Use the exposed PostgreSQL replication-lag metric and Service Bus `ReplicationLagDuration` to record the latest values/timestamps in Azure Monitor. A missing metric is **unknown**, not zero. Also compare a known marker row; lag dashboards alone do not prove a particular business record arrived.

On the secondary management host, use a fresh Entra token only for the verification period:

```bash
(
  PGHOST=$(jq -er '.fullyQualifiedDomainName' <<< "$Replica")
  PGUSER=$(jq -er '.userPrincipalName' <<< "$Admin")
  export PGHOST PGUSER
  export PGDATABASE='ordersdb' PGSSLMODE='verify-full' PGSSLROOTCERT='system'
  PGPASSWORD=$(az account get-access-token --resource https://ossrdbms-aad.database.windows.net --query accessToken -o tsv)
  export PGPASSWORD
  trap 'unset PGPASSWORD' EXIT
  psql -X --set ON_ERROR_STOP=1 -c 'SELECT pg_is_in_recovery();'
  psql -X --set ON_ERROR_STOP=1 -c 'SELECT order_id,item FROM processed_orders ORDER BY order_id;'
  psql -X --set ON_ERROR_STOP=1 -c "SELECT grantee,privilege_type FROM information_schema.role_table_grants WHERE table_schema='public' AND table_name='processed_orders' AND grantee IN ('orders_api_dr','orders_worker_dr') ORDER BY grantee,privilege_type;"
)
```

Expect recovery mode `t`, the saved durable ID with its original item, API SELECT and worker INSERT/SELECT. Check the initialization's object-ID mapping against secondary identity outputs as well as SQL grants. A role with a matching name but the wrong object ID is not successful authentication. Capture the metric's sample timestamp and aggregation; no sample is not a successful readiness gate.

Use a `psql`/libpq build that supports `PGSSLROOTCERT=system`. Otherwise point `PGSSLROOTCERT` at the host's approved CA PEM bundle (for example `/etc/ssl/certs/ca-certificates.crt` on Debian/Ubuntu); keep `PGSSLMODE=verify-full`. Do not substitute an unverified TLS mode.

</details>

## 5. Reconcile a passive application without allowing a second writer

**Challenge:** Reconcile a separate, least-privilege secondary GitOps application while proving it cannot start another writer. Retain the rendered manifest, regional source/credential scope, owner registration and live zero-replica evidence.

This is a **warm platform/cold application** lab, not active/active. Both application replica counts must remain **zero**, with no secondary HPA/KEDA resources. Resolve PostgreSQL from the secondary DNS view and use local PE addresses, immutable image digests and no passwords.

**Scaler identity boundary:** lab 6's primary KEDA operator uses the separate `$(lab_value Prefix)-scaler` UAI from deployment `scaling-identity`, not the foundation worker identity. Preserve that scaler client ID, operator federation and `TriggerAuthentication/servicebus-workload` throughout primary fencing/failback; the fencing patch changes none of them. The secondary helper copies `k8s/base`, **not** the primary GitOps application directory containing scaling assets, and rejects rendered HPA/KEDA authentication resources or `__SCALER_CLIENT_ID__`. Consequently this required fixed-replica secondary has no KEDA operator configuration or scaler client ID to substitute, and creates no secondary scaler. Its worker retains receiver-only Service Bus permission. Adding secondary autoscaling would be a separate change requiring an independently provisioned scaler identity, secondary-issuer operator federation and correct shared-queue scope; never reuse the primary scaler or substitute the worker client ID.

Use the **existing lab-4 repository**, a new secondary cluster path and a new scoped read-only runtime credential. Verify owner/name/access before bootstrap, which can otherwise create a misspelled repository. Use the actual default branch/personal-account flag as appropriate. Mirror the same Flux controller version through the authorized private runner; no direct `ghcr.io` pulls. Any temporary bootstrap branch-policy exception must be narrowly approved and removed. Revoke the replaced bootstrap PAT, rotate runtime credentials, and keep normal changes on `publish-reviewed-change.sh`.

Namespace/RBAC creation remains platform-owned. Persist the app CR and secondary RBAC in the **secondary** bootstrap root's explicit resource list, never the primary scope.

<details>
<summary>Solution</summary>

The helper copies the shared base, patches regional Workload IDs and dependencies, pins the digest and sets both deployments to zero without autoscalers:

```bash
ReplicaAddresses=$(getent ahostsv4 "$(jq -er '.fullyQualifiedDomainName' <<< "$Replica")")
PgReplicaIp=$(awk 'NR == 1 {print $1}' <<< "$ReplicaAddresses")
[[ -n "$PgReplicaIp" ]] || { printf 'Replica DNS returned no IPv4 address.\n' >&2; exit 1; }
bash ./advanced/new-regional-overlay.sh --output-directory ./advanced/regions/secondary \
  --registry-server "$(lab_value RegistryServer)" --image-digest "$ImageDigest" --service-bus-namespace "$(lab_value ServiceBusName).servicebus.windows.net" \
  --api-client-id "$(jq -er '.apiClientId.value' <<< "$Secondary")" --worker-client-id "$(jq -er '.workerClientId.value' <<< "$Secondary")" \
  --postgres-host "$(jq -er '.fullyQualifiedDomainName' <<< "$Replica")" --postgres-private-ip "$PgReplicaIp"
kubectl kustomize ./advanced/regions/secondary > ./.artifacts/advanced/secondary-rendered.yaml
grep -nE '__|image:|replicas:' ./.artifacts/advanced/secondary-rendered.yaml
```

Resolve the replica IP **on the secondary DNS view**. Required result: no unresolved placeholders, both replicas zero, immutable digest, local PE addresses. No PostgreSQL password. The helper's HTTPS egress is enforced by the regional firewall; it is not destination allowlisting on its own.

Bootstrap Flux in the secondary with the **existing lab-4 repository** and its HTTPS/read-only-runtime-PAT mechanism, a **new cluster path** (`gitops/clusters/secondary`), and **new scoped repository credential**. Do not reuse `gitops/clusters/primary` or reconcile primary resources into both clusters. Verify the existing repository first, then bootstrap:

Use the same Flux CLI/controller version mirrored in lab 4. If upgrading that version, run `ops/mirror-flux-images.sh` again on the authorized private Podman host and verify the new controller digests before bootstrapping; the secondary firewall does not allow direct `ghcr.io` pulls. Apply the same narrowly approved temporary bootstrap branch-policy exception as lab 4, then remove it. Normal application/platform changes continue through `publish-reviewed-change.sh`, not direct pushes to protected `main`.

```bash
read -r -p 'Existing GitHub owner: ' GitOwner
read -r -p 'Existing lab 4 repository name: ' GitRepository
GitUrl="https://github.com/$GitOwner/$GitRepository.git"
git ls-remote "$GitUrl" HEAD
GitBranch=$(gh repo view "$GitOwner/$GitRepository" --json defaultBranchRef --jq .defaultBranchRef.name)
OwnerType=$(gh api "users/$GitOwner" --jq .type)
PersonalArgs=()
if [[ "$OwnerType" == User ]]; then PersonalArgs=(--personal); fi
(
  trap 'unset GITHUB_TOKEN' EXIT
  read -rs -p 'Short-lived existing-repo bootstrap PAT (masked): ' GITHUB_TOKEN
  printf '\n'
  export GITHUB_TOKEN
  flux bootstrap github --context "$SecondaryContext" --owner "$GitOwner" --repository "$GitRepository" \
    --branch "$GitBranch" --path gitops/clusters/secondary --token-auth --registry "$(lab_value RegistryServer)/fluxcd" "${PersonalArgs[@]}"
)
(
  trap 'unset RuntimePat' EXIT
  read -rs -p 'Separate existing-repo Contents-read-only runtime PAT (masked): ' RuntimePat
  printf '\n'
  flux create secret git flux-system --context "$SecondaryContext" -n flux-system \
    --url "$GitUrl" --username git --password "$RuntimePat"
)
kubectl --context "$SecondaryContext" apply -f ./advanced/secondary-rbac.yaml
git pull --ff-only
git add ./advanced/regions
bash ./advanced/publish-reviewed-change.sh --message "Prepare fenced secondary orders recovery"
flux create kustomization orders-secondary --context "$SecondaryContext" -n flux-system \
  --source GitRepository/flux-system --path ./advanced/regions/secondary --prune --interval 1m --service-account orders-reconciler
flux reconcile kustomization orders-secondary --context "$SecondaryContext" -n flux-system --with-source
kubectl --context "$SecondaryContext" get deployment -n orders
```

Use the actual default branch if not `main`; add `--personal` for a personal-account repository as in lab 4. Revoke the bootstrap PAT after replacing it, not the runtime PAT; schedule rotation. `flux bootstrap github` can create a repository if misspelled: verify the existing owner/name and access **before executing**. This pack does not ask you to create a repository. Persist the secondary app CR and `secondary-rbac.yaml` in `gitops/clusters/secondary`; keep them out of primary bootstrap scope. Namespace creation is platform-owned; the generated app base deliberately excludes its namespace manifest.

</details>

## 6. Publish only private, TLS-validated regional origins

**Challenge:** Publish the two regional origins through Front Door Premium Private Link, enforce trusted TLS and WAF, and prove that the secondary remains disabled until data activation. Persist the origin owners in the correct Git roots and identify any alternate ingress bypass.

**Safety gates:** Approve only the two verified Front Door endpoint requests. Keep both load balancers private, use distinct owned origin names with CA-issued certificates, and never commit private keys or disable certificate-name verification. Keep namespace/RBAC/origin resources platform-owned. Do not manually edit AKS-managed load balancers, use a public origin as a shortcut, or treat a failed health probe as a writer fence.

**Exit evidence:** Retain private origin IPs, certificate validation, reviewed endpoint approvals, Git owner registrations, a normal 200, the controlled WAF 403 and its matching log. Record whether the old internal route was retired or deliberately retained as a trusted-network bypass.

<details>
<summary>Solution</summary>

Required supported design: **Front Door Premium → approved Private Link → Standard internal Load Balancer + Private Link Service → unprivileged TLS reverse proxy → order-api**. It does not rely on unverified Private Link integration with the lab-3 managed Gateway implementation. PLS requires Standard LB backend type `nodeIPConfiguration`; check before provisioning:

```bash
az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --query networkProfile.loadBalancerProfile
az aks show -g "$SecondaryRg" -n "$SecondaryCluster" --query networkProfile.loadBalancerProfile
```

If backend type is `nodeIP`, run `az aks update -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --load-balancer-backend-pool-type nodeIPConfiguration` (and the equivalent secondary command) in a planned change and validate traffic; PLS does not support `nodeIP`. Baseline should already be compatible. Do not manually modify AKS-managed load balancer resources.

Obtain two distinct owned origin DNS names and valid CA-issued full certificate chains/private keys. **Do not reuse `$(lab_value Hostname)` from lab 3:** its exact-name private DNS zone and apex A record intentionally resolve to the old internal Gateway and would shadow that name for VNet-connected clients. Use separate names such as `origin-primary.<owned-domain>` and `origin-secondary.<owned-domain>`; the Front Door client URL is the separate generated `azurefd.net` endpoint (or a separately configured custom domain). Origin DNS names need not expose public origins; they supply SNI/Host validation. The lab-3 self-signed certificate's local trust and 14-day lifetime do not make it publicly trusted by Front Door. `new-lab-certificate.sh` accepts `--hostname` and writes `rendered/certs/tls.crt` and `rendered/certs/tls.key`; those lab certificates are not suitable here. Keep the new CA-issued certificates in an excluded local folder.

```bash
read -r -p 'CA-certified primary origin FQDN: ' PrimaryOriginHost
read -r -p 'CA-certified secondary origin FQDN: ' SecondaryOriginHost
InternalHostname=$(jq -r '.Hostname // ""' <<< "$Lab")
if [[ -z "$PrimaryOriginHost" || -z "$SecondaryOriginHost" ||
      "${PrimaryOriginHost,,}" == "${SecondaryOriginHost,,}" ||
      "${PrimaryOriginHost,,}" == "${InternalHostname,,}" ||
      "${SecondaryOriginHost,,}" == "${InternalHostname,,}" ]]; then
  printf 'Choose two distinct owned origin names, both different from the lab 3 private hostname.\n' >&2
  exit 1
fi
read -r -p 'Primary full-chain PEM path: ' PrimaryCert
read -r -p 'Primary private-key PEM path: ' PrimaryKey
read -r -p 'Secondary full-chain PEM path: ' SecondaryCert
read -r -p 'Secondary private-key PEM path: ' SecondaryKey
kubectl --context "$PrimaryContext" create secret tls regional-origin-tls -n orders --cert "$PrimaryCert" --key "$PrimaryKey" --dry-run=client -o yaml |
  kubectl --context "$PrimaryContext" apply -f -
kubectl --context "$SecondaryContext" create secret tls regional-origin-tls -n orders --cert "$SecondaryCert" --key "$SecondaryKey" --dry-run=client -o yaml |
  kubectl --context "$SecondaryContext" apply -f -
```

This explicit local-secret bootstrap is not a secret committed to Git. Production uses a regional certificate lifecycle/secrets integration and rehearses rotation. The proxy reads files at startup; roll it after certificate renewal and verify TLS.

Mirror the approved unprivileged proxy image through the lab-4 private build runner and pin its digest. Do not assume the firewall permits Docker Hub pulls:

```bash
# On the authorized private registry runner with Podman, using its approved auth:
podman pull docker.io/nginxinc/nginx-unprivileged:stable-alpine
podman tag docker.io/nginxinc/nginx-unprivileged:stable-alpine "$(lab_value RegistryServer)/regional-origin:lab"
bash ./scripts/connect-acr-podman.sh --registry-name "$(lab_value AcrName)" --registry-server "$(lab_value RegistryServer)"
podman push "$(lab_value RegistryServer)/regional-origin:lab"
ProxyDigest=$(az acr repository show -n "$(lab_value AcrName)" --image regional-origin:lab --query digest -o tsv)
[[ "$ProxyDigest" =~ ^sha256:[a-f0-9]{64}$ ]] || { printf 'Invalid mirrored proxy digest.\n' >&2; exit 1; }
ProxyImage="$(lab_value RegistryServer)/regional-origin@$ProxyDigest"
bash ./advanced/new-origin-manifest.sh --origin-host "$PrimaryOriginHost" \
  --output-directory ./advanced/origins/primary --image "$ProxyImage"
bash ./advanced/new-origin-manifest.sh --origin-host "$SecondaryOriginHost" \
  --output-directory ./advanced/origins/secondary --image "$ProxyImage"
```

`stable-alpine` is only the upstream discovery tag: review the resolved server version, image provenance and vulnerability result, then deploy the mirrored **digest**. This is an unprivileged NGINX web-server reverse proxy, not the retired ingress-nginx Kubernetes controller. Do not infer controller retirement applies to every NGINX server image.

Add a `kustomization.yaml` in each origin directory with `resources: [origin.yaml]`, commit, push, and bootstrap separate platform owners:

```bash
for Region in primary secondary; do
cat > "./advanced/origins/$Region/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - origin.yaml
YAML
  kubectl kustomize "./advanced/origins/$Region" > /dev/null
done
git add ./advanced/origins
bash ./advanced/publish-reviewed-change.sh --message "Add private TLS origins for Front Door"
flux create kustomization origin-primary --context "$PrimaryContext" -n "$FluxNamespace" --source "GitRepository/$GitSource" --path ./advanced/origins/primary --prune --interval 1m
flux create kustomization origin-secondary --context "$SecondaryContext" -n flux-system --source GitRepository/flux-system --path ./advanced/origins/secondary --prune --interval 1m
kubectl --context "$PrimaryContext" get service regional-origin -n orders
kubectl --context "$SecondaryContext" get service regional-origin -n orders
```

Persist these owner CRs in their corresponding cluster bootstrap paths using lab 7's export-and-register recipe, with the appropriate `--context` on each export/reconciliation. Register `origin-primary.yaml` in the primary root's explicit resource list. Register `origin-secondary.yaml`, the exported `orders-secondary.yaml` and a copy of `advanced/secondary-rbac.yaml` in the secondary root's explicit resource list. Merely placing these files beside `kustomization.yaml` does not activate them. Leave the app child namespace-scoped; namespace/RBAC creation remains platform-owned. Secondary proxy readiness will fail while `order-api` is scaled to zero; this is deliberate. Both LoadBalancer addresses must be **private**.

```bash
PrimaryNodeRg=$(az aks show -g "$(lab_value ResourceGroup)" -n "$(lab_value ClusterName)" --query nodeResourceGroup -o tsv)
SecondaryNodeRg=$(az aks show -g "$SecondaryRg" -n "$SecondaryCluster" --query nodeResourceGroup -o tsv)
PrimaryPls=$(az network private-link-service show -g "$PrimaryNodeRg" -n orders-origin --query id -o tsv)
SecondaryPls=$(az network private-link-service show -g "$SecondaryNodeRg" -n orders-origin --query id -o tsv)
az deployment group create -g "$(lab_value ResourceGroup)" -n frontdoor -f ./advanced/frontdoor.bicep \
  -p "profileName=$FrontDoor" "primaryHost=$PrimaryOriginHost" "secondaryHost=$SecondaryOriginHost" \
  "primaryPlsId=$PrimaryPls" "secondaryPlsId=$SecondaryPls" \
  "primaryLocation=$(lab_value Location)" "secondaryLocation=$(lab_value SecondaryLocation)"
az network private-endpoint-connection list --id "$PrimaryPls" -o json
az network private-endpoint-connection list --id "$SecondaryPls" -o json
```

The manifest uses PLS visibility `*` so the Front Door managed endpoint can request a connection across subscription boundaries; **visibility is not approval or public network access**. Automatic approval is deliberately absent. Inspect the requester's endpoint IDs/subscription and compare with the new Front Door profile's pending requests. **Approve only those exact two**, never loop over arbitrary pending connections:

```bash
read -r -p 'Verified Front Door pending connection ARM ID on primary PLS: ' PrimaryConnectionId
read -r -p 'Verified Front Door pending connection ARM ID on secondary PLS: ' SecondaryConnectionId
az network private-endpoint-connection approve --id "$PrimaryConnectionId" --description 'Reviewed Front Door lab origin'
az network private-endpoint-connection approve --id "$SecondaryConnectionId" --description 'Reviewed Front Door lab origin'
EdgeHost=$(az deployment group show -g "$(lab_value ResourceGroup)" -n frontdoor --query properties.outputs.endpointHost.value -o tsv)
EdgeUrl="https://$EdgeHost"
NormalStatus=$(curl --fail-with-body --max-time 30 "$EdgeUrl/readyz" \
  --output ./.artifacts/advanced/edge-readyz.json --write-out '%{http_code}')
[[ "$NormalStatus" == 200 ]] || { printf 'Expected readiness HTTP 200, got %s.\n' "$NormalStatus" >&2; exit 1; }
# A deliberate 403 is evidence, not a curl --fail success path.
WafStatus=$(curl --max-time 30 "$EdgeUrl/readyz" --header 'X-Lab-Waf-Test: block' \
  --output ./.artifacts/advanced/waf-response.txt --write-out '%{http_code}')
[[ "$WafStatus" == 403 ]] || { printf 'Expected controlled WAF 403, got %s.\n' "$WafStatus" >&2; exit 1; }
```

Expected primary normal response 200 and WAF test 403; save WAF log showing `ControlledWafTest`. Managed default/bot rules and custom test rule run in Prevention. Secondary origin is **Disabled** in ARM until the data activation gate. Front Door probes only readiness, not database writeability or queue recovery.

The template does not configure diagnostic export. Enable the WAF category on the Front Door profile, then repeat the controlled request so there is a record to correlate:

```bash
FrontDoorResourceId=$(az afd profile show -g "$(lab_value ResourceGroup)" --profile-name "$FrontDoor" --query id -o tsv)
az monitor diagnostic-settings categories list --resource "$FrontDoorResourceId" -o table
WafLogs='[{"category":"FrontDoorWebApplicationFirewallLog","enabled":true}]'
az monitor diagnostic-settings create --name lab-frontdoor-waf --resource "$FrontDoorResourceId" \
  --workspace "$(jq -er '.workspaceId.value' <<< "$Primary")" --logs "$WafLogs"
WafTestUtc=$(date -u +%FT%TZ)
printf '%s\n' "$WafTestUtc" > ./.artifacts/advanced/waf-test-utc.txt
WafStatus=$(curl --max-time 30 "$EdgeUrl/readyz" --header 'X-Lab-Waf-Test: block' \
  --output ./.artifacts/advanced/waf-logged-response.txt --write-out '%{http_code}')
[[ "$WafStatus" == 403 ]] || { printf 'Expected controlled WAF 403, got %s.\n' "$WafStatus" >&2; exit 1; }
```

Confirm the category exists in the live listing before creating the setting. After ingestion, in the target Log Analytics workspace filter `AzureDiagnostics` to this profile's resource ID, category `FrontDoorWebApplicationFirewallLog`, and the recorded UTC interval. Retain the entry naming `ControlledWafTest` and action `Block`; an unrelated 403 or no ingested record is incomplete evidence. Include the diagnostic setting in final telemetry cleanup.

**Review alternate entry points:** lab 3's `Gateway/gateway-system/orders-gateway` uses the `approuting-istio` class and an **internal** load balancer; its `HTTPRoute/orders/orders` is not a public-origin solution. This capstone creates a separate supported ILB/PLS origin rather than turning that Gateway public or assuming it supports Front Door Private Link directly. No public/DNAT variant is required for this path.

If Front Door must be the only supported application entry point, retire the old direct internal Gateway route from its authoritative platform source. If lab 3's objects are still manually platform-owned, remove these two named resources explicitly. Otherwise remove them through their Flux source, and ensure no controller recreates them. Retain the namespace/certificate until final cleanup:

```bash
# Only if lab 3's Gateway/HTTPRoute are still manual and not subsequently Git-owned:
kubectl --context "$PrimaryContext" delete httproute orders -n orders
kubectl --context "$PrimaryContext" delete gateway orders-gateway -n gateway-system
kubectl --context "$PrimaryContext" get svc -A
kubectl --context "$PrimaryContext" get gateway,httproute -A
```

If direct internal management access is deliberately retained instead, record it as a trusted-network bypass of Front Door WAF—not as WAF-protected traffic. Check external direct-origin requests fail even with forged Host/`X-Azure-FDID`. PLS is not publicly routable; only the approved private endpoint connects Front Door. Internal VNet callers remain in the trusted network boundary—apply NSG/network policy constraints if they must also be forbidden. Do not use FDID header filtering alone as authentication. A future public/DNAT origin alternative would require an explicitly supported public listener, valid TLS, `AzureFrontDoor.Backend` source restrictions **and** validation of the exact Front Door profile ID; simply assigning a DNS name to this internal Gateway would not work.

When every origin is unhealthy, Front Door may route rather than become an absolute traffic fence: **Disabled origins and stopped writers are the incident controls**, not probe failure alone.

</details>

## 7. Execute a supported staged Fleet update independently of DR

**Challenge:** Run a supported staged update, observe the secondary-first sequence and soak, and retain each member's before/after image IDs and outcome. Distinguish an actual replacement from a no-op and demonstrate the stop decision for a failed validation.

**Safety gates:** Do not overlap this task with the regional failover exercise. Agree member maintenance windows and one upgrade owner; a timer is not human approval. Use supported GA stages, not preview failure tolerances, and stop on failed health/business checks.

<details>
<summary>Solution</summary>

```bash
az extension add -n fleet --upgrade
az extension show -n fleet --query version
az fleet create -g "$(lab_value ResourceGroup)" -n "$Fleet" -l "$(lab_value Location)" --enable-managed-identity
az fleet member create -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n primary \
  --member-cluster-id "$(jq -er '.clusterId.value' <<< "$Primary")" --update-group primary
az fleet member create -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n secondary \
  --member-cluster-id "$(jq -er '.clusterId.value' <<< "$Secondary")" --update-group secondary
Run="images-$(date -u +%Y%m%d%H%M)"
az fleet updaterun create -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n "$Run" \
  --upgrade-type NodeImageOnly --node-image-selection Latest --stages ./advanced/fleet-stages.json
az fleet updaterun start -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n "$Run"
az fleet updaterun show -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n "$Run" -o json
```

Use CLI ≥2.82 and fleet extension ≥1.8.3 per current quickstart. No hub is needed for update orchestration. The JSON uses GA stages/groups and a 600-second soak, **not preview maxAllowedFailures or approval features**. A timer is not a human approval gate.

Fleet respects member maintenance windows. Arrange overlapping approved windows and no competing cluster automatic upgrades before starting. While secondary stage executes, verify node versions, add-on health and image pull; during soak verify primary orders and stop the run on failure:

```bash
az fleet updaterun stop -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n "$Run"
# Only run stop on an actual failed validation; an in-progress node operation may still finish.
```

For a successful exercise do not stop; wait until both member statuses are successful and save their image IDs. If already latest, record no-op and repeat after an available image release, or select a supported common Kubernetes target and use `Full --kubernetes-version <target>`. A no-op is not an actual image replacement. Fleet resources in the primary region are not part of the emergency traffic/data failover path.

Capture each cluster's node-pool image IDs using lab 9 task 5 before starting and after the corresponding stage. Match those records to the member status in `updaterun show`, not just the run's creation result. During secondary validation require healthy system add-ons and private image access while the app remains passive; verify primary order processing during soak. If any gate fails, record the failure and stop the run rather than waiting for the timer to advance.

</details>

## 8. Create pending business work, fence primary, and declare the incident

**Challenge:** Record a known replicated order, accumulate exactly the bounded synthetic batch, declare the incident, and fence primary traffic, replicas and autoscalers in both live and Git state. Retain the accepted-ID/item ledger, incident UTC time and fencing SHA.

**Safety gates:** No Fleet/AKS maintenance or other test producers may be running. Do not delete the queue or alter working identities. Suspend only the named app/autoscaler owners, keep security/platform reconciliation active, and do not promote data until both regions' application writers are stopped and primary fencing is durable.

<details>
<summary>Solution</summary>

Do not run this during Fleet/AKS maintenance. First record a durable lab-8 order that already exists in the replica. Then deliberately accumulate a bounded Service Bus backlog.

Identify **all** Flux owners of the app, HPA and KEDA from lab 4–6. Suspend those named Kustomizations explicitly and record them; leave security/platform owners active:

```bash
flux get kustomizations --context "$PrimaryContext" -A
kubectl --context "$PrimaryContext" get scaledobjects,hpa -n orders
IncidentOwners=("$AppKustomization") # Lab 6 puts HPA/KEDA specifications under this same child.
printf '%s\n' "${IncidentOwners[@]}" > ./.artifacts/advanced/incident-owners.txt
for Owner in "${IncidentOwners[@]}"; do
  flux suspend kustomization "$Owner" --context "$PrimaryContext" -n "$FluxNamespace"
done
# Pause every worker ScaledObject in this application namespace before scaling its target.
kubectl --context "$PrimaryContext" annotate scaledobject --all -n orders autoscaling.keda.sh/paused-replicas=0 --overwrite
kubectl --context "$PrimaryContext" scale deployment order-worker -n orders --replicas=0
kubectl --context "$PrimaryContext" get pods -n orders
```

Wait for workers zero; KEDA's own controller must observe the pause. Do not delete the queue. If autoscaler APIs were not installed, skip the ScaledObject command only after confirming there is no worker autoscaler.

```bash
if [[ -e ./.artifacts/advanced/dr-accepted.json ]]; then
  printf 'Existing accepted ledger: reconcile the interrupted exercise instead of overwriting it.\n' >&2
  exit 1
fi
Batch='[]'
for Number in {1..20}; do
  OrderId="dr-$(tr -d '-' < /proc/sys/kernel/random/uuid)"
  Batch=$(jq --arg id "$OrderId" --arg item "widget-$Number" '. + [{id: $id, item: $item}]' <<< "$Batch")
done
printf '%s\n' "$Batch" > ./.artifacts/advanced/dr-batch.json
Accepted='[]'
printf '%s\n' "$Accepted" > ./.artifacts/advanced/dr-accepted.json
for Number in {0..19}; do
  Order=$(jq -c --argjson index "$Number" '.[$index]' <<< "$Batch")
  curl --fail-with-body --max-time 30 "$EdgeUrl/orders" --header 'Content-Type: application/json' \
    --data "$Order" --output "./.artifacts/advanced/dr-submit-$Number.json"
  Accepted=$(jq --argjson order "$Order" '. + [$order]' <<< "$Accepted")
  printf '%s\n' "$Accepted" > ./.artifacts/advanced/dr-accepted.json
done
az servicebus queue show -g "$(lab_value ResourceGroup)" --namespace-name "$(lab_value ServiceBusName)" -n orders --query countDetails \
  -o json > ./.artifacts/advanced/queue-before-incident.json
cat ./.artifacts/advanced/queue-before-incident.json
IncidentStart=$(date -u +%FT%TZ)
printf '%s\n' "$IncidentStart" > ./.artifacts/advanced/incident-start.txt
# ARM traffic fence, remove the API scale controller, then stop primary API.
az afd origin update -g "$(lab_value ResourceGroup)" --profile-name "$FrontDoor" --origin-group-name orders --origin-name primary --enabled-state Disabled
# orders is suspended; the durable Git fence below also removes this HPA from the effective render.
kubectl --context "$PrimaryContext" delete hpa order-api -n orders --ignore-not-found
kubectl --context "$PrimaryContext" scale deployment order-api -n orders --replicas=0
kubectl --context "$PrimaryContext" get hpa -n orders
kubectl --context "$PrimaryContext" get deploy,pods -n orders
```

Expected backlog ≥20 active messages, both primary workloads stopped, secondary app still zero, both edge origins disabled. Confirm no test producers remain. This models **regional application unavailability plus deliberate service promotions**. It does not destroy Azure networking or claim to reproduce a real regional platform outage.

Fencing must be durable before data promotion: update the **primary Git desired state** to API/worker zero and pause/disable its autoscalers, commit/push, while keeping its owners suspended until the recovery authority approves. Otherwise a restarted Flux controller could recreate old writers on recovery. Record the fencing commit SHA.

```bash
bash ./ops/add-gitops-file.sh --source ./advanced/primary-fence.yaml --kind Patch --namespace orders
git add ./gitops/clusters/primary/apps/orders
bash ./advanced/publish-reviewed-change.sh --message "Fence primary app and KEDA before regional promotion"
git rev-parse HEAD | tee ./.artifacts/advanced/primary-fencing-sha.txt
```

The supplied patch targets lab 6's `ScaledObject/order-worker` and removes `HorizontalPodAutoscaler/order-api` from the effective Git render with a strategic `$patch: delete`. If a customer renamed either object, update those exact targets before rendering. Require the rendered API HPA to be absent, the worker ScaledObject paused at zero, both deployments at zero, and no live API HPA before promotion. Do not rely on HPA's zero-replica behavior as a durable writer fence. Do not remove the fencing patch while secondary is active.

Inspect desired and live state before authorizing task 9:

```bash
kubectl kustomize ./gitops/clusters/primary/apps/orders > ./.artifacts/advanced/primary-fenced.yaml
kubectl --context "$PrimaryContext" get deployment,hpa,scaledobject -n orders -o yaml
kubectl --context "$SecondaryContext" get deployment,pods -n orders
az afd origin list -g "$(lab_value ResourceGroup)" --profile-name "$FrontDoor" --origin-group-name orders \
  --query '[].{name:name,state:enabledState}' -o table
```

Check the two named application Deployments rather than requiring the separate TLS proxy to stop. `order-api` and `order-worker` must have zero desired and actual replicas and no remaining terminating application pods; the primary worker ScaledObject must be paused at zero and the API HPA absent. Both Front Door origins must report Disabled. A saved fencing SHA without the corresponding live writer shutdown is not a completed gate.

</details>

## 9. Promote data and messages, then admit secondary application traffic

**Challenge:** Perform planned messaging/database promotions, verify the recovered marker and grants, activate only the secondary application, then prove every accepted ID/item and SQL uniqueness before opening edge traffic. Measure business recovery and backlog completion separately, test duplicate redelivery, and process a new secondary order.

**Safety gates:** Keep primary Git fencing and suspensions intact. The required exercise does not force data loss. Never enable the secondary origin based solely on `/readyz`; require writable promoted data, the right queue region and verified business records. Missing IDs or unknown replication lag must remain explicit blockers.

<details>
<summary>Solution</summary>

Because source services remain accessible, use **planned** operations. Do not force loss for this required path:

```bash
az servicebus namespace failover -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" \
  --primary-location "$(lab_value SecondaryLocation)" --force false
az servicebus namespace show -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" --query geoDataReplication
az postgres flexible-server replica promote -g "$SecondaryRg" -n "$PgReplica" \
  --promote-mode standalone --promote-option planned --yes
az postgres flexible-server show -g "$SecondaryRg" -n "$PgReplica" --query '{state:state,role:replicationRole,host:fullyQualifiedDomainName}'
```

PostgreSQL standalone promotion deliberately leaves the old primary as a separate server. It does **not** automatically demote/fence the old server. The primary app is zero in Git and suspended, and edge primary remains Disabled. Before secondary enablement, use secondary `psql` to require `pg_is_in_recovery() = false`, presence of the previously durable marker, correct SQL grants and TLS. Verify Service Bus reports the selected primary region and active backlog via the same stable FQDN/local PE.

Edit only `advanced/regions/secondary/kustomization.yaml` replica counts: API 2 and worker 1. Keep its DB host pointing to the now promoted `$PgReplica`. Commit/push/reconcile:

```bash
bash ./advanced/set-regional-replicas.sh --api 2 --worker 1
kubectl kustomize ./advanced/regions/secondary | grep 'replicas:'
git add ./advanced/regions/secondary
bash ./advanced/publish-reviewed-change.sh --message "Activate secondary after planned data and message promotion"
flux reconcile kustomization orders-secondary --context "$SecondaryContext" -n flux-system --with-source
kubectl --context "$SecondaryContext" rollout status deployment/order-api -n orders --timeout=600s
kubectl --context "$SecondaryContext" rollout status deployment/order-worker -n orders --timeout=600s
kubectl --context "$SecondaryContext" logs deployment/order-worker -n orders --tail=100
```

Wait for backlog to drain and inspect database rows **before** enabling edge traffic. Use private management access/port-forward for the business check if necessary; don't create a public Service:

```bash
# Separate private-host terminal; leave this attached only for the verification period:
set -euo pipefail
source ./scripts/use-lab.sh
read -r -p 'Recorded secondary kubeconfig context from task 1: ' SecondaryContext
[[ -n "$SecondaryContext" ]] || { printf 'A recorded context is required.\n' >&2; exit 1; }
kubectl --context "$SecondaryContext" port-forward -n orders service/order-api 18080:80 --address 127.0.0.1
```

In the original terminal on the same host:

```bash
Accepted=$(cat ./.artifacts/advanced/dr-accepted.json)
AcceptedLines=$(jq -ec '.[]' <<< "$Accepted")
while IFS= read -r Order; do
  OrderId=$(jq -er '.id' <<< "$Order")
  curl --fail-with-body --max-time 30 "http://127.0.0.1:18080/orders/$OrderId"
done <<< "$AcceptedLines"
bash ./advanced/test-order-ledger.sh --base-uri http://127.0.0.1:18080 --timeout-seconds 180
date -u +%FT%TZ > ./.artifacts/advanced/backlog-completed.txt
```

Require all 20 IDs and expected items, and one SQL row per ID. Then:

Use task 4's fresh-token `psql` environment against the promoted replica. For each accepted ID, run the parameterized count query from lab 8 task 3; require the original item and `copies = 1`. Retain that SQL evidence alongside `test-order-ledger.sh`'s HTTP report: the helper checks ID/item values but deliberately does not claim SQL uniqueness.

```bash
az afd origin update -g "$(lab_value ResourceGroup)" --profile-name "$FrontDoor" --origin-group-name orders --origin-name secondary --enabled-state Enabled
curl --fail-with-body --max-time 30 "$EdgeUrl/readyz"
while IFS= read -r Order; do
  OrderId=$(jq -er '.id' <<< "$Order")
  curl --fail-with-body --max-time 30 "$EdgeUrl/orders/$OrderId"
done <<< "$AcceptedLines"
bash ./advanced/test-order-ledger.sh --base-uri "$EdgeUrl" --timeout-seconds 180
Recovered=$(date -u +%FT%TZ)
IncidentStart=$(cat ./.artifacts/advanced/incident-start.txt)
RtoSeconds=$(( $(date -u -d "$Recovered" +%s) - $(date -u -d "$IncidentStart" +%s) ))
# Lost=0 is valid only after the HTTP ledger AND SQL ID/item/uniqueness gates above.
jq -n --arg recovered "$Recovered" --argjson rto "$RtoSeconds" --argjson accepted "$Accepted" \
  '{Recovered: $recovered, RtoSeconds: $rto, Accepted: ($accepted | length), Lost: 0, TargetMet: ($rto <= 1800)}' |
  tee ./.artifacts/advanced/recovery-result.json
```

Write `Lost=0` **only after verifying every recorded ID/item**. Re-submit one identical accepted order, confirm a single database row, then create a new order through Front Door and verify worker/database completion. Check dead-letter count is unchanged. Record HTTP recovery time separately from "all accepted orders processed" time. Missing IDs are an investigation, not permission to report success.

Use the saved payload for redelivery and add the newly accepted secondary order to the ledger so failback cannot accidentally validate only pre-incident data:

```bash
curl --fail-with-body --max-time 30 "$EdgeUrl/orders" --header 'Content-Type: application/json' \
  --data "$(jq -ec '.[0]' <<< "$Accepted")" --output ./.artifacts/advanced/duplicate-submit.json
SecondaryOrder=$(jq -n --arg id "secondary-$(tr -d '-' < /proc/sys/kernel/random/uuid)" \
  '{id: $id, item: "synthetic-after-promotion"}')
printf '%s\n' "$SecondaryOrder" > ./.artifacts/advanced/secondary-order.json
curl --fail-with-body --max-time 30 "$EdgeUrl/orders" --header 'Content-Type: application/json' \
  --data "$SecondaryOrder" --output ./.artifacts/advanced/secondary-submit.json
Accepted=$(jq --argjson order "$SecondaryOrder" '. + [$order]' <<< "$Accepted")
printf '%s\n' "$Accepted" > ./.artifacts/advanced/dr-accepted.json
bash ./advanced/test-order-ledger.sh --base-uri "$EdgeUrl" \
  --report-path ./.artifacts/advanced/secondary-verification.json --timeout-seconds 180
az servicebus queue show -g "$(lab_value ResourceGroup)" --namespace-name "$(lab_value ServiceBusName)" -n orders --query countDetails
```

Require the repeated ID's duplicate-processing log and one SQL row with its original item; the new secondary ID must also have one row. Compare dead-letter count with the pre-incident baseline, including the intentional conflicting-ID evidence from lab 8. Compare actual elapsed recovery with the agreed 1,800-second target and report a breach honestly, even when all IDs eventually arrive. Inspect secondary pod `imageID`, private DNS resolution and pull events against the approved digest to close task 3's deferred image-pull evidence.

**Real inaccessible-region variant (discussion, not a required destructive action):** incident command weighs lag/data loss and fencing confidence before `--force true` for Service Bus or `--promote-option forced` for PostgreSQL. A network partition is not proof the old writer is dead. Microsoft recommends deleting/recreating the old Service Bus region after forced promotion rather than trusting resynchronization. Reconcile accepted-but-missing messages from an independent durable producer ledger/outbox; the lab's local evidence file is **not** a production recovery system.

</details>

## 10. Fail back with a new replica, not by pointing at stale data

**Challenge:** Return service to the original region using a new replica of the promoted database, a planned writer handover, updated authoritative endpoints and restored autoscaler ownership. Verify the entire ledger, including the secondary-created order, and restore replication protection.

**Safety gates:** The original database is stale after standalone promotion; do not point back to it or replay the original database deployment. Fence secondary producers and writers before planned return promotions. Keep primary ingress disabled until private business checks succeed; never permit two active writers or permanently return replica ownership to Flux.

<details>
<summary>Solution</summary>

Do not re-enable primary origin just because `/readyz` becomes healthy. After standalone promotion, the old `$Pg` is stale. While secondary remains active, create a **new** PostgreSQL replica in the primary region from the promoted secondary:

```bash
PgReturn="$(lab_value Prefix)-pg-return"
Promoted=$(az postgres flexible-server show -g "$SecondaryRg" -n "$PgReplica" -o json)
az postgres flexible-server replica create -g "$(lab_value ResourceGroup)" -n "$PgReturn" \
  --source-server "$(jq -er '.id' <<< "$Promoted")" --location "$(lab_value Location)"
ReturnServer=$(az postgres flexible-server show -g "$(lab_value ResourceGroup)" -n "$PgReturn" -o json)
az postgres flexible-server update -g "$(lab_value ResourceGroup)" -n "$PgReturn" --public-access Disabled
bash ./advanced/new-private-endpoint.sh --resource-group "$(lab_value ResourceGroup)" --location "$(lab_value Location)" \
  --name "$(lab_value Prefix)-pg-return-pe" --resource-id "$(jq -er '.id' <<< "$ReturnServer")" --group-id postgresqlServer \
  --subnet-id "$(jq -er '.endpointsSubnetId.value' <<< "$Primary")" --vnet-id "$(jq -er '.vnetId.value' <<< "$Primary")" --zone-name privatelink.postgres.database.azure.com
az postgres flexible-server microsoft-entra-admin create -g "$(lab_value ResourceGroup)" -s "$PgReturn" --object-id "$(jq -er '.id' <<< "$Admin")" --display-name "$(jq -er '.userPrincipalName' <<< "$Admin")" --type User
```

Wait for healthy replication and a secondary-created order visible on the return replica. Verify primary roles `orders_api` / `orders_worker` still map to the original identities. Schedule a short write outage for the planned return:

1. Disable the secondary Front Door origin; stop all producers.
2. Wait for secondary queue active count zero and in-flight processing settled. Require every accepted ID present in SQL.
3. Change secondary source replica counts to zero, commit/push/reconcile; verify no secondary worker/API pods.
4. Promote the return replica **planned**, then promote Service Bus back **planned**.

```bash
az afd origin update -g "$(lab_value ResourceGroup)" --profile-name "$FrontDoor" --origin-group-name orders --origin-name secondary --enabled-state Disabled
# Wait for zero backlog and settled in-flight work before fencing compute:
az servicebus queue show -g "$(lab_value ResourceGroup)" --namespace-name "$(lab_value ServiceBusName)" -n orders --query countDetails
bash ./advanced/set-regional-replicas.sh --api 0 --worker 0
git add ./advanced/regions/secondary
bash ./advanced/publish-reviewed-change.sh --message "Fence secondary for planned failback"
flux reconcile kustomization orders-secondary --context "$SecondaryContext" -n flux-system --with-source
kubectl --context "$SecondaryContext" get deployment,pods -n orders
# Continue only when no secondary API/worker pods remain:
az postgres flexible-server replica promote -g "$(lab_value ResourceGroup)" -n "$PgReturn" \
  --promote-mode standalone --promote-option planned --yes
az servicebus namespace failover -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" \
  --primary-location "$(lab_value Location)" --force false
```

Update the authoritative **primary** app `POSTGRES_HOST` to `$(jq -er '.fullyQualifiedDomainName' <<< "$ReturnServer")`, update the TCP 5432 PE IP allowlist, and retain `orders_api`/`orders_worker`. Removing the temporary fencing patch must restore `HorizontalPodAutoscaler/order-api` from its unchanged source resource and **leave Deployment replicas omitted from normal primary Git source**, as established in lab 6: HPA owns API scale and KEDA owns worker activation. Preserve their existing specifications. Remove the incident KEDA pause from live resources **and source**, resume only the recorded `orders` child and reconcile:

```bash
ReturnAddresses=$(getent ahostsv4 "$(jq -er '.fullyQualifiedDomainName' <<< "$ReturnServer")")
ReturnIp=$(awk 'NR == 1 {print $1}' <<< "$ReturnAddresses")
[[ -n "$ReturnIp" ]] || { printf 'Return-server DNS returned no IPv4 address.\n' >&2; exit 1; }
bash ./advanced/set-primary-database.sh --host-name "$(jq -er '.fullyQualifiedDomainName' <<< "$ReturnServer")" --private-ip "$ReturnIp"
bash ./ops/remove-gitops-file.sh --name primary-fence.yaml --namespace orders
git add ./gitops/clusters/primary/apps/orders
bash ./advanced/publish-reviewed-change.sh --message "Return primary to the recovered database and remove writer fence"
kubectl --context "$PrimaryContext" annotate scaledobject --all -n orders autoscaling.keda.sh/paused-replicas-
mapfile -t IncidentOwners < ./.artifacts/advanced/incident-owners.txt
for Owner in "${IncidentOwners[@]}"; do
  flux resume kustomization "$Owner" --context "$PrimaryContext" -n "$FluxNamespace"
  flux reconcile kustomization "$Owner" --context "$PrimaryContext" -n "$FluxNamespace" --with-source
done
kubectl --context "$PrimaryContext" get hpa order-api -n orders
# Explicit one-time failback activation, not a permanent replica value in Git.
# HPA does not awaken an API deliberately left at zero; do not assume removing a patch does so.
kubectl --context "$PrimaryContext" scale deployment order-api -n orders --replicas=2
kubectl --context "$PrimaryContext" scale deployment order-worker -n orders --replicas=1
kubectl --context "$PrimaryContext" rollout status deployment/order-api -n orders --timeout=600s
kubectl --context "$PrimaryContext" rollout status deployment/order-worker -n orders --timeout=600s
```

The two scale commands are recorded operational activation seeds after the writer-ownership handover; subsequent scale belongs to HPA/KEDA, not Flux. Require no `spec.replicas` or replica transformer in the normal primary source after fence removal. If source still includes zero replicas, simply removing a KEDA pause is not a durable repair; inspect desired objects and scaled-object triggers. KEDA may legitimately return to zero after draining. Verify database writeability, the entire accepted ledger and a new primary order over the private management path before enabling primary origin:

```bash
az afd origin update -g "$(lab_value ResourceGroup)" --profile-name "$FrontDoor" --origin-group-name orders --origin-name primary --enabled-state Enabled
curl --fail-with-body --max-time 30 "$EdgeUrl/readyz"
Accepted=$(cat ./.artifacts/advanced/dr-accepted.json)
AcceptedLines=$(jq -ec '.[]' <<< "$Accepted")
while IFS= read -r Order; do
  OrderId=$(jq -er '.id' <<< "$Order")
  curl --fail-with-body --max-time 30 "$EdgeUrl/orders/$OrderId"
done <<< "$AcceptedLines"
bash ./advanced/test-order-ledger.sh --base-uri "$EdgeUrl" --report-path ./.artifacts/advanced/failback-verification.json
kubectl --context "$SecondaryContext" get deployment -n orders
az servicebus namespace show -g "$(lab_value ResourceGroup)" -n "$(lab_value ServiceBusName)" --query geoDataReplication
```

Expected primary active, secondary API/worker zero, primary edge Enabled/secondary Disabled, queue promoted back and all orders present. **The active primary database is now `$PgReturn`, not `$Pg`.** Update operational configuration/inventory and future IaC strategy; do not rerun the old `postgres` deployment and silently point back to an empty or stale server. Re-establish a read replica from the new active database to restore protection after the exercise; standalone promotions break the former replication relationship.

For reprotection, repeat task 4's supported replica-create/private-endpoint/Entra-admin sequence with `$(jq -er '.id' <<< "$ReturnServer")` as the source and a new, explicitly recorded secondary replica name. Keep that replica read-only, verify the new primary's marker there and record lag. Do not reattach an old standalone database by merely changing a hostname. If immediately proceeding to approved final teardown, record reprotection as intentionally omitted for decommissioning, not restored DR readiness.

</details>

## 11. Deliver the customer decision and safely decommission

Deliver a UTC timeline, infrastructure/output IDs, Git SHAs, certificate validation/private-origin evidence, WAF block evidence, Fleet member outcomes, both promotions, all accepted IDs/items, duplicate test, replication lag observations, measured RTO/RPO, failback result and new authoritative database name.

**Challenge:** Present a go/no-go recovery decision supported by the measured timeline and ledger, answer the customer questions, then retire only the approved disposable footprint in dependency order.

**Safety gates:** Do not delete the secondary while it hosts the active database or Service Bus primary. Require successful failback or explicit approval to delete synthetic business data; enumerate resource groups and consumers first. Preserve required backup retention and evidence. Never reset shared subscription policy/Defender settings or delete shared registries/DNS zones blindly.

<details>
<summary>Solution: recovery decision and final teardown</summary>

A completed planned exercise has a verified writer handover, every accepted ID/item and one row per business key, the duplicate test, a new order after each activation, and a measured recovery duration compared with the signed target. Report forced-region loss as an unexecuted discussion variant, not tested zero-loss resilience. A Fleet no-op, pending image-pull evidence or omitted reprotection remains explicitly qualified in the handover.

If you ran [Lab 11: KAITO inference](11-kaito.md), complete its Workspace and GPU-pool cleanup before the end-of-pack teardown below. Do not leave an active model Workspace or assume deleting its namespace removed its Azure GPU compute.

At end-of-pack teardown:

1. Confirm successful failback or obtain explicit approval to delete all synthetic business data. Stop producers; disable both Front Door origins.
2. Stop active Fleet runs; remove member registrations (`az fleet member delete -g "$(lab_value ResourceGroup)" --fleet-name "$Fleet" -n secondary --yes`, likewise primary), then delete the Fleet resource.
3. Remove regional app/origin Kustomizations through their Git bootstrap sources and prune **before** deleting clusters so AKS cleans up PLS/load balancers. Delete Front Door/security policy/WAF after traffic is intentionally retired; revoke private endpoint connections.
4. Remove Service Bus secondary replication location only after zero backlog and verified promotion state; use `namespace update --locations` with a reviewed single Primary entry. This deletes the removed region's replica data. If finishing everything, delete the namespace only as part of final foundation teardown.
5. Keep active `$PgReturn` and any required replica/backup until retention expires. Delete stale `$Pg` / promoted `$PgReplica` only after checking no workload endpoint references them. Remove their PEs, not shared private DNS zone links still serving other databases.
6. Remove ACR geo-replication only after all secondary digest consumers are gone; do not delete shared ACR while primary still runs. Delete secondary scoped identities/role assignments, private endpoints and the secondary RG last (`az group delete -n "$SecondaryRg"`) only after enumerating its resources and confirming the disposable boundary; retain the CLI's deletion confirmation.
7. Follow lab 8's backup retention/extension/snapshot teardown **before** removing the primary cluster. Preserve the evidence bundle; securely delete local TLS private keys and auth material through the approved workstation process. No subscription-wide policy/Defender state is blindly reset.

</details>

<details>
<summary>Model answer: Why not active/active?</summary>

Competing writers require conflict semantics, ownership and distributed-state design. This service has one active worker region and an explicit write-fencing protocol; a routing weight is not a concurrency control.

</details>

<details>
<summary>Model answer: Why not automatic failover whenever a probe fails?</summary>

A readiness failure does not identify database correctness, queue replication or whether the original writer is alive. Operator/data gates avoid split brain; automation must encode and verify those gates.

</details>

<details>
<summary>Model answer: Does synchronous Service Bus give zero-loss orders?</summary>

It improves message replication semantics, but database replication is separate. A completed message whose row was not replicated can still cause a cross-system gap in a real forced event. Use a durable outbox/inbox/reconciliation design for stronger guarantees.

</details>

<details>
<summary>Model answer: Why Fleet?</summary>

It provides repeatable update sequencing and status across members. It is neither a global ingress controller for this application nor a database/message recovery engine.

</details>

<details>
<summary>Model answer: When is this worth it?</summary>

When quantified business loss and recovery requirements justify two-region capacity, replication latency/cost, certificates, DNS, security operations, on-call authority and repeated rehearsals. Zone redundancy plus restore may be adequate for less stringent requirements.

</details>

## Official references and status

Checked **2026-09-10**: [Service Bus Geo-Replication](https://learn.microsoft.com/azure/service-bus-messaging/service-bus-geo-replication) (non-partitioned Premium required; partitioned support preview), [GA namespace CLI](https://learn.microsoft.com/cli/azure/servicebus/namespace), [GA PostgreSQL replica CLI](https://learn.microsoft.com/cli/azure/postgres/flexible-server/replica), [Front Door private ILB origin](https://learn.microsoft.com/azure/frontdoor/standard-premium/how-to-enable-private-link-internal-load-balancer), [origin security](https://learn.microsoft.com/azure/frontdoor/origin-security), [AKS ILB/PLS restrictions](https://learn.microsoft.com/azure/aks/internal-lb), [Fleet quickstart](https://learn.microsoft.com/azure/kubernetes-fleet/quickstart-create-fleet-and-members), [Fleet update orchestration](https://learn.microsoft.com/azure/kubernetes-fleet/update-orchestration). Some Service Bus documentation examples use preview ARM schemas; this required path uses current **GA core CLI** commands and excludes partitioned preview replication. Front Door origin managed-identity authentication and Fleet preview failure tolerances are not prerequisites.
