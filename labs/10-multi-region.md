# Lab 10 — Recover the business service across regions, then fail back

**Customer:** a regional failure must not become an unbounded data-integrity incident. **Time:** one or two days, including replication/provisioning. **Cost:** two AKS platforms/firewalls, replicated Service Bus Premium, two PostgreSQL servers, Front Door Premium and WAF, ACR replication, private endpoints and traffic. Obtain an explicit budget/change approval before execution.

This capstone depends on labs 1–9. All commands run from the workspace root in PowerShell 7. Use a VNet-connected management host **in each region** or approved independently resilient private connectivity/DNS. Do not use a primary-region VM as the only recovery control point. No public AKS API, admin credentials, public database, or connection strings are introduced.

## 1. Sign the recovery contract before provisioning

Define roles: incident commander authorizes promotion; platform operator fences traffic/compute; database owner authorizes PostgreSQL promotion; messaging owner authorizes namespace promotion; app owner validates accepted order IDs; scribe records UTC evidence. In this synthetic lab one operator may fill all roles explicitly.

Adopt these **lab targets**, not Azure guarantees: RTO ≤30 minutes after declaration; planned exercise RPO zero for the recorded accepted IDs. For an actual inaccessible-region event, asynchronous database replication can lose recent rows and a forced messaging promotion can lose messages or acknowledgements. Record actual lag and reconcile both systems. PostgreSQL and Service Bus do **not** share a distributed commit.

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

```powershell
. .\scripts\Use-Lab.ps1
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
New-Item .\.artifacts\advanced -ItemType Directory -Force | Out-Null
$Primary = az deployment group show -g $Lab.ResourceGroup -n foundation --query properties.outputs -o json | ConvertFrom-Json
$SecondaryRg = "$($Lab.ResourceGroup)-secondary"
$SecondaryPrefix = "$($Lab.Prefix)dr" # Must remain <=12 characters for foundation.
if ($SecondaryPrefix.Length -gt 12) { throw 'Choose a unique 4–12 character SecondaryPrefix.' }
$SecondaryCluster = "$SecondaryPrefix-aks"
$Pg = "$($Lab.Prefix)-pg"
$PgReplica = "$($Lab.Prefix)-pg-dr"
$FrontDoor = "$($Lab.Prefix)-afd"
$Fleet = "$($Lab.Prefix)-fleet"
$FluxNamespace = 'flux-system'
# Use the exact primary app/source names recorded in labs 4/7.
$AppKustomization = 'orders'
$GitSource = 'flux-system'
$PrimaryContext = "$($Lab.ClusterName)-primary"
$SecondaryContext = "$SecondaryCluster-secondary"
az aks get-credentials -g $Lab.ResourceGroup -n $Lab.ClusterName --context $PrimaryContext --overwrite-existing
```

Requirements: GA regional AKS version, PostgreSQL General Purpose replica support and quota, Service Bus non-partitioned Premium geo-replication, Front Door Private Link origin region support, x86 Linux, valid public-CA TLS certificates for two owned origin names. A self-signed lab-3 certificate **cannot** be used with Front Door Private Link certificate validation. Use DNS-01 issuance through your approved CA process; do not open an origin just for certificate validation.

## 2. Reproduce the second platform from the same foundation

Select a supported version available in **both** regions, compatible with lab 9, and confirm zones/SKU availability:

The current `infra\main.bicep` exposes `networkPrefix` as the first two address octets. This lab uses `10.60` for the secondary spoke and `10.70` for its firewall hub; primary remains `10.40`/`10.50`. Do not replace this with an invented parameter name or reuse overlapping ranges. If the foundation parameter is renamed later, update the invocation and validate the rendered subnet ranges before deployment.

```powershell
az aks get-versions -l $Lab.SecondaryLocation -o table
az postgres flexible-server list-skus -l $Lab.SecondaryLocation -o table
$Cluster = az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName -o json | ConvertFrom-Json
$KubernetesVersion = $Cluster.kubernetesVersion
$AdminGroupId = $Cluster.aadProfile.adminGroupObjectIDs[0]
$PublicKeyPath = '.\rendered\keys\aks.pub'
$SshPublicKey = (Get-Content $PublicKeyPath -Raw).Trim()
az group create -n $SecondaryRg -l $Lab.SecondaryLocation
az deployment group create -g $SecondaryRg -n foundation -f .\infra\main.bicep `
  -p "prefix=$SecondaryPrefix" "location=$($Lab.SecondaryLocation)" "kubernetesVersion=$KubernetesVersion" `
  "adminGroupObjectId=$AdminGroupId" "sshPublicKey=$SshPublicKey" networkPrefix=10.60
$Secondary = az deployment group show -g $SecondaryRg -n foundation --query properties.outputs -o json | ConvertFrom-Json
az deployment group create -g $SecondaryRg -n private-endpoints -f .\infra\private-endpoints.bicep `
  -p "prefix=$SecondaryPrefix" "location=$($Lab.SecondaryLocation)" "vnetId=$($Secondary.vnetId.value)" `
  "subnetId=$($Secondary.endpointsSubnetId.value)" "acrId=$($Secondary.acrId.value)" `
  "keyVaultId=$($Secondary.keyVaultId.value)" "serviceBusId=$($Secondary.serviceBusId.value)"
az deployment group create -g $SecondaryRg -n egress -f .\infra\firewall.bicep `
  -p "prefix=$SecondaryPrefix" "location=$($Lab.SecondaryLocation)" "spokeName=$SecondaryPrefix-vnet" `
  spokeAddress=10.60.0.0/16 nodesAddress=10.60.0.0/20 hubPrefix=10.70
```

Repeat the **route-table association and AKS outbound-type transition** from lab 3 with secondary outputs, not the primary VNet. Inspect the firewall deployment output and apply the precise node subnet route association:

```powershell
$SecondaryEgress = az deployment group show -g $SecondaryRg -n egress --query properties.outputs -o json | ConvertFrom-Json
az role assignment create --assignee-object-id $Secondary.clusterPrincipalId.value `
  --assignee-principal-type ServicePrincipal --role 'Network Contributor' --scope $SecondaryEgress.routeTableId.value
az network vnet subnet update --ids $Secondary.nodesSubnetId.value --route-table $SecondaryEgress.routeTableId.value
az aks update -g $SecondaryRg -n $SecondaryCluster --outbound-type userDefinedRouting
az acr update -n $Secondary.acrName.value --public-network-enabled false
az keyvault update -n $Secondary.keyVaultName.value --public-network-access Disabled
az servicebus namespace update -g $SecondaryRg -n $Secondary.serviceBusName.value --public-network-access Disabled
az aks get-credentials -g $SecondaryRg -n $SecondaryCluster --context $SecondaryContext --overwrite-existing
kubectl --context $SecondaryContext get nodes -o wide
```

Configure regional management connectivity/private DNS before the final command. Secondary foundation creates a separate ACR, Key Vault and Service Bus for reproducibility. **The orders DR workload will instead use the primary geo-replicated ACR and primary geo-replicated Service Bus namespace.** The unused secondary namespace/registry incur costs; retain until the exercise is validated, then delete only after proving no consumers reference them. Never treat two unrelated Service Bus namespaces as replicas.

Apply labs 5/7 observability and platform guardrails in the secondary through their parameterized deployment/Git paths, pointing telemetry at approved regional or central sinks. Record any omitted sensor/add-on as an explicit coverage gap. Fleet updates need supported add-ons in both regions, not merely two Ready clusters.

## 3. Make images, identities and secrets survive primary loss

```powershell
az acr replication create --registry $Lab.AcrName --location $Lab.SecondaryLocation
az acr replication list --registry $Lab.AcrName -o table
$SecondaryKubelet = az aks show -g $SecondaryRg -n $SecondaryCluster --query identityProfile.kubeletidentity.objectId -o tsv
az role assignment create --assignee-object-id $SecondaryKubelet --assignee-principal-type ServicePrincipal `
  --role AcrPull --scope $Primary.acrId.value
.\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $SecondaryRg -Location $Lab.SecondaryLocation `
  -Name "$SecondaryPrefix-shared-acr-pe" -ResourceId $Primary.acrId.value -GroupId registry `
  -SubnetId $Secondary.endpointsSubnetId.value -VnetId $Secondary.vnetId.value -ZoneName privatelink.azurecr.io
```

ACR geo-replication creates regional data endpoints. Reinspect **both** ACR private endpoint DNS-zone groups after creating the replica; verify registry and secondary-region data FQDNs resolve through the local PE, and ensure secondary nodes pull the exact digest without crossing a primary PE. A private DNS zone group must include all required ACR records. Do not accept "ACR replication Succeeded" as an image-pull test.

```powershell
$ApprovedImage = kubectl --context $PrimaryContext get deployment order-api -n orders -o jsonpath='{.spec.template.spec.containers[0].image}'
$ApprovedWorker = kubectl --context $PrimaryContext get deployment order-worker -n orders -o jsonpath='{.spec.template.spec.containers[0].image}'
if ($ApprovedImage -ne $ApprovedWorker -or $ApprovedImage -notlike "$($Lab.RegistryServer)/order-app@sha256:*") {
  throw 'Primary API and worker must use the same approved registry digest from lab 4.'
}
$ImageDigest = ($ApprovedImage -split '@')[1]
if ($ImageDigest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Resolve the approved app digest from the private registry first.' }
az acr repository show -n $Lab.AcrName --image "order-app@$ImageDigest" --query digest -o tsv
$BusId = $Primary.serviceBusId.value
az role assignment create --assignee-object-id $Secondary.apiPrincipalId.value --assignee-principal-type ServicePrincipal --role 'Azure Service Bus Data Sender' --scope "$BusId/queues/orders"
az role assignment create --assignee-object-id $Secondary.workerPrincipalId.value --assignee-principal-type ServicePrincipal --role 'Azure Service Bus Data Receiver' --scope "$BusId/queues/orders"
.\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $SecondaryRg -Location $Lab.SecondaryLocation `
  -Name "$SecondaryPrefix-shared-bus-pe" -ResourceId $BusId -GroupId namespace `
  -SubnetId $Secondary.endpointsSubnetId.value -VnetId $Secondary.vnetId.value -ZoneName privatelink.servicebus.windows.net
```

Each cluster uses its own issuer and regional user-assigned identities from foundation; do not reuse a primary issuer federation blindly. Regional Service Bus private DNS zones link only their own VNets; a shared zone with two same-name endpoint records can strand clients on a failed region.

Regenerate synthetic lab configuration in the secondary Key Vault from its authoritative source, using that vault's regional PE and access policy/RBAC. For real secrets, decide and rehearse supported backup/restore, replication or regeneration under the vault's geo/tenant constraints. Workload IDs remove Service Bus/PostgreSQL passwords; they do not remove TLS private keys. The base DR app does not mount the lab-2 sample secret; if your current image requires it, reproduce the SecretProviderClass/mount against the secondary vault **before admitting traffic**.

## 4. Establish actual message and database replication

Inspect current namespace partitioning first:

```powershell
az servicebus namespace show -g $Lab.ResourceGroup -n $Lab.ServiceBusName `
  --query '{sku:sku,partitions:premiumMessagingPartitions,replication:geoDataReplication}' -o json
```

Required path is **non-partitioned Premium** (one messaging partition). Partitioned namespace geo-replication is still preview in the current docs. If this namespace is partitioned, stop and migrate the synthetic exercise to a supported non-partitioned Premium namespace with the same identity/network controls; do not call the preview path GA.

```powershell
@(
  @{'location-name'=$Lab.Location;'role-type'='Primary'}
  @{'location-name'=$Lab.SecondaryLocation;'role-type'='Secondary'}
) | ConvertTo-Json -Depth 5 | Set-Content .\.artifacts\advanced\bus-locations.json
az servicebus namespace update -g $Lab.ResourceGroup -n $Lab.ServiceBusName `
  --locations '@.\.artifacts\advanced\bus-locations.json' --max-replication-lag-duration-in-seconds 0
az servicebus namespace show -g $Lab.ResourceGroup -n $Lab.ServiceBusName --query geoDataReplication -o json
```

`0` selects synchronous replication for this zero-loss **planned** queue exercise; assess cross-region publish latency and availability trade-offs. Wait for secondary **Ready**. Geo-Replication copies message data/state; `georecovery-alias` / metadata-only Geo-DR does not. Do not configure both features on the same namespace.

Map secondary application identities into PostgreSQL **before** creating the replica so SQL roles are replicated:

```powershell
$Admin = az ad signed-in-user show -o json | ConvertFrom-Json
$PgPrimary = az postgres flexible-server show -g $Lab.ResourceGroup -n $Pg -o json | ConvertFrom-Json
.\advanced\Initialize-OrdersDatabase.ps1 -HostName $PgPrimary.fullyQualifiedDomainName -AdminLogin $Admin.userPrincipalName `
  -ApiPrincipalId $Secondary.apiPrincipalId.value -WorkerPrincipalId $Secondary.workerPrincipalId.value `
  -ApiRole orders_api_dr -WorkerRole orders_worker_dr
az postgres flexible-server replica create -g $SecondaryRg -n $PgReplica --source-server $PgPrimary.id --location $Lab.SecondaryLocation
$Replica = az postgres flexible-server show -g $SecondaryRg -n $PgReplica -o json | ConvertFrom-Json
az postgres flexible-server update -g $SecondaryRg -n $PgReplica --public-access Disabled
.\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $SecondaryRg -Location $Lab.SecondaryLocation `
  -Name "$SecondaryPrefix-pg-pe" -ResourceId $Replica.id -GroupId postgresqlServer `
  -SubnetId $Secondary.endpointsSubnetId.value -VnetId $Secondary.vnetId.value -ZoneName privatelink.postgres.database.azure.com
az postgres flexible-server microsoft-entra-admin create -g $SecondaryRg -s $PgReplica --object-id $Admin.id --display-name $Admin.userPrincipalName --type User
```

Verify replica health, replication lag and known lab-8 rows over TLS from the secondary management host. Use the lab-8 `psql` token environment; `SELECT pg_is_in_recovery();` must be true before promotion. The server's Entra administrator/control-plane settings are checked independently from replicated SQL roles.

```powershell
az monitor metrics list-definitions --resource $PgPrimary.id -o table
az monitor metrics list-definitions --resource $BusId -o table
```

Use the exposed PostgreSQL replication-lag metric and Service Bus `ReplicationLagDuration` to record the latest values/timestamps in Azure Monitor. A missing metric is **unknown**, not zero. Also compare a known marker row; lag dashboards alone do not prove a particular business record arrived.

## 5. Reconcile a passive application without allowing a second writer

The helper creates a region-specific copy of the shared base manifests, patches regional Workload IDs, pins the image digest, configures the database and dependencies, and sets both replica counts to **zero**. It deliberately excludes HPA/KEDA, so they cannot awaken the passive worker. This is a **warm platform/cold application** lab, not a pre-running active/active implementation.

**Scaler identity boundary:** lab 6's primary KEDA operator uses the separate `${Lab.Prefix}-scaler` UAI from deployment `scaling-identity`, not the foundation worker identity. Preserve that scaler client ID, operator federation and `TriggerAuthentication/servicebus-workload` throughout primary fencing/failback; the fencing patch changes none of them. The secondary helper copies `k8s\base`, **not** the primary GitOps application directory containing scaling assets, and rejects rendered HPA/KEDA authentication resources or `__SCALER_CLIENT_ID__`. Consequently this required fixed-replica secondary has no KEDA operator configuration or scaler client ID to substitute, and creates no secondary scaler. Its worker retains receiver-only Service Bus permission. Adding secondary autoscaling would be a separate change requiring an independently provisioned scaler identity, secondary-issuer operator federation and correct shared-queue scope; never reuse the primary scaler or substitute the worker client ID.

```powershell
$PgReplicaIp = (Resolve-DnsName $Replica.fullyQualifiedDomainName -Type A | Where-Object IPAddress | Select-Object -First 1).IPAddress
.\advanced\New-RegionalOverlay.ps1 -OutputDirectory .\advanced\regions\secondary `
  -RegistryServer $Lab.RegistryServer -ImageDigest $ImageDigest -ServiceBusNamespace "$($Lab.ServiceBusName).servicebus.windows.net" `
  -ApiClientId $Secondary.apiClientId.value -WorkerClientId $Secondary.workerClientId.value `
  -PostgresHost $Replica.fullyQualifiedDomainName -PostgresPrivateIp $PgReplicaIp
kubectl kustomize .\advanced\regions\secondary | Set-Content .\.artifacts\advanced\secondary-rendered.yaml
Select-String -Path .\.artifacts\advanced\secondary-rendered.yaml -Pattern '__|image:|replicas:'
```

Resolve the replica IP **on the secondary DNS view**. Required result: no unresolved placeholders, both replicas zero, immutable digest, local PE addresses. No PostgreSQL password. The helper's HTTPS egress is enforced by the regional firewall; it is not destination allowlisting on its own.

Bootstrap Flux in the secondary with the **existing lab-4 repository** and its HTTPS/read-only-runtime-PAT mechanism, a **new cluster path** (`gitops/clusters/secondary`), and **new scoped repository credential**. Do not reuse `gitops/clusters/primary` or reconcile primary resources into both clusters. Verify the existing repository first, then bootstrap:

Use the same Flux CLI/controller version mirrored in lab 4. If upgrading that version, run `ops\Mirror-FluxImages.ps1` again on the authorized private Docker host and verify the new controller digests before bootstrapping; the secondary firewall does not allow direct `ghcr.io` pulls. Apply the same narrowly approved temporary bootstrap branch-policy exception as lab 4, then remove it. Normal application/platform changes continue through `Publish-ReviewedChange.ps1`, not direct pushes to protected `main`.

```powershell
$GitOwner = Read-Host 'Existing GitHub owner'
$GitRepository = Read-Host 'Existing lab 4 repository name'
$GitUrl = "https://github.com/$GitOwner/$GitRepository.git"
git ls-remote $GitUrl HEAD
if ($LASTEXITCODE -ne 0) { throw 'Existing repository could not be verified.' }
$env:GITHUB_TOKEN = Read-Host 'Short-lived existing-repo bootstrap PAT (masked)' -MaskInput
try {
  flux bootstrap github --context $SecondaryContext --owner $GitOwner --repository $GitRepository `
    --branch main --path gitops/clusters/secondary --token-auth --registry "$($Lab.RegistryServer)/fluxcd"
} finally { Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue }
$RuntimePat = Read-Host 'Separate existing-repo Contents-read-only runtime PAT (masked)' -MaskInput
try {
  flux create secret git flux-system --context $SecondaryContext -n flux-system `
    --url $GitUrl --username git --password $RuntimePat
} finally { $RuntimePat = $null }
kubectl --context $SecondaryContext apply -f .\advanced\secondary-rbac.yaml
git pull --ff-only
git add .\advanced\regions
.\advanced\Publish-ReviewedChange.ps1 -Message "Prepare fenced secondary orders recovery"
flux create kustomization orders-secondary --context $SecondaryContext -n flux-system `
  --source GitRepository/flux-system --path ./advanced/regions/secondary --prune --interval 1m --service-account orders-reconciler
flux reconcile kustomization orders-secondary --context $SecondaryContext -n flux-system --with-source
kubectl --context $SecondaryContext get deployment -n orders
```

Use the actual default branch if not `main`; add `--personal` for a personal-account repository as in lab 4. Revoke the bootstrap PAT after replacing it, not the runtime PAT; schedule rotation. `flux bootstrap github` can create a repository if misspelled: verify the existing owner/name and access **before executing**. This pack does not ask you to create a repository. Persist the secondary app CR and `secondary-rbac.yaml` in `gitops/clusters/secondary`; keep them out of primary bootstrap scope. Namespace creation is platform-owned; the generated app base deliberately excludes its namespace manifest.

## 6. Publish only private, TLS-validated regional origins

Required supported design: **Front Door Premium → approved Private Link → Standard internal Load Balancer + Private Link Service → unprivileged TLS reverse proxy → order-api**. It does not rely on unverified Private Link integration with the lab-3 managed Gateway implementation. PLS requires Standard LB backend type `nodeIPConfiguration`; check before provisioning:

```powershell
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query networkProfile.loadBalancerProfile
az aks show -g $SecondaryRg -n $SecondaryCluster --query networkProfile.loadBalancerProfile
```

If backend type is `nodeIP`, run `az aks update -g $Lab.ResourceGroup -n $Lab.ClusterName --load-balancer-backend-pool-type nodeIPConfiguration` (and the equivalent secondary command) in a planned change and validate traffic; PLS does not support `nodeIP`. Baseline should already be compatible. Do not manually modify AKS-managed load balancer resources.

Obtain two distinct owned origin DNS names and valid CA-issued full certificate chains/private keys. **Do not reuse `$Lab.Hostname` from lab 3:** its exact-name private DNS zone and apex A record intentionally resolve to the old internal Gateway and would shadow that name for VNet-connected clients. Use separate names such as `origin-primary.<owned-domain>` and `origin-secondary.<owned-domain>`; the Front Door client URL is the separate generated `azurefd.net` endpoint (or a separately configured custom domain). Origin DNS names need not expose public origins; they supply SNI/Host validation. The lab-3 self-signed certificate's Windows user trust and 14-day lifetime do not make it publicly trusted by Front Door. Keep the new certificates in an excluded local folder.

```powershell
$PrimaryOriginHost = Read-Host 'CA-certified primary origin FQDN'
$SecondaryOriginHost = Read-Host 'CA-certified secondary origin FQDN'
$InternalHostname = ''
if ($Lab.PSObject.Properties.Name -contains 'Hostname') { $InternalHostname = $Lab.Hostname }
if (-not $PrimaryOriginHost -or -not $SecondaryOriginHost -or
    $PrimaryOriginHost -ieq $SecondaryOriginHost -or
    @($PrimaryOriginHost,$SecondaryOriginHost) -contains $InternalHostname) {
  throw 'Choose two distinct owned origin names, both different from the lab 3 private hostname.'
}
$PrimaryCert = Read-Host 'Primary full-chain PEM path'
$PrimaryKey = Read-Host 'Primary private-key PEM path'
$SecondaryCert = Read-Host 'Secondary full-chain PEM path'
$SecondaryKey = Read-Host 'Secondary private-key PEM path'
kubectl --context $PrimaryContext create secret tls regional-origin-tls -n orders --cert $PrimaryCert --key $PrimaryKey --dry-run=client -o yaml |
  kubectl --context $PrimaryContext apply -f -
kubectl --context $SecondaryContext create secret tls regional-origin-tls -n orders --cert $SecondaryCert --key $SecondaryKey --dry-run=client -o yaml |
  kubectl --context $SecondaryContext apply -f -
```

This explicit local-secret bootstrap is not a secret committed to Git. Production uses a regional certificate lifecycle/secrets integration and rehearses rotation. The proxy reads files at startup; roll it after certificate renewal and verify TLS.

Mirror the approved unprivileged proxy image through the lab-4 private build runner and pin its digest. Do not assume the firewall permits Docker Hub pulls:

```powershell
# On the authorized private registry runner with Docker, using its approved auth:
docker pull nginxinc/nginx-unprivileged:stable-alpine
docker tag nginxinc/nginx-unprivileged:stable-alpine "$($Lab.RegistryServer)/regional-origin:lab"
az acr login -n $Lab.AcrName
docker push "$($Lab.RegistryServer)/regional-origin:lab"
$ProxyDigest = az acr repository show -n $Lab.AcrName --image regional-origin:lab --query digest -o tsv
$ProxyImage = "$($Lab.RegistryServer)/regional-origin@$ProxyDigest"
.\advanced\New-OriginManifest.ps1 -OriginHost $PrimaryOriginHost `
  -OutputDirectory .\advanced\origins\primary -Image $ProxyImage
.\advanced\New-OriginManifest.ps1 -OriginHost $SecondaryOriginHost `
  -OutputDirectory .\advanced\origins\secondary -Image $ProxyImage
```

`stable-alpine` is only the upstream discovery tag: review the resolved server version, image provenance and vulnerability result, then deploy the mirrored **digest**. This is an unprivileged NGINX web-server reverse proxy, not the retired ingress-nginx Kubernetes controller. Do not infer controller retirement applies to every NGINX server image.

Add a `kustomization.yaml` in each origin directory with `resources: [origin.yaml]`, commit, push, and bootstrap separate platform owners:

```powershell
foreach ($Region in 'primary','secondary') {
@'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - origin.yaml
'@ | Set-Content ".\advanced\origins\$Region\kustomization.yaml"
}
git add .\advanced\origins
.\advanced\Publish-ReviewedChange.ps1 -Message "Add private TLS origins for Front Door"
flux create kustomization origin-primary --context $PrimaryContext -n $FluxNamespace --source "GitRepository/$GitSource" --path ./advanced/origins/primary --prune --interval 1m
flux create kustomization origin-secondary --context $SecondaryContext -n flux-system --source GitRepository/flux-system --path ./advanced/origins/secondary --prune --interval 1m
kubectl --context $PrimaryContext get service regional-origin -n orders
kubectl --context $SecondaryContext get service regional-origin -n orders
```

Persist these owner CRs in their corresponding cluster bootstrap paths using lab 7's export-and-register recipe, with the appropriate `--context` on each export/reconciliation. Register `origin-primary.yaml` in the primary root's explicit resource list. Register `origin-secondary.yaml`, the exported `orders-secondary.yaml` and a copy of `advanced\secondary-rbac.yaml` in the secondary root's explicit resource list. Merely placing these files beside `kustomization.yaml` does not activate them. Leave the app child namespace-scoped; namespace/RBAC creation remains platform-owned. Secondary proxy readiness will fail while `order-api` is scaled to zero; this is deliberate. Both LoadBalancer addresses must be **private**.

```powershell
$PrimaryNodeRg = az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query nodeResourceGroup -o tsv
$SecondaryNodeRg = az aks show -g $SecondaryRg -n $SecondaryCluster --query nodeResourceGroup -o tsv
$PrimaryPls = az network private-link-service show -g $PrimaryNodeRg -n orders-origin --query id -o tsv
$SecondaryPls = az network private-link-service show -g $SecondaryNodeRg -n orders-origin --query id -o tsv
az deployment group create -g $Lab.ResourceGroup -n frontdoor -f .\advanced\frontdoor.bicep `
  -p "profileName=$FrontDoor" "primaryHost=$PrimaryOriginHost" "secondaryHost=$SecondaryOriginHost" `
  "primaryPlsId=$PrimaryPls" "secondaryPlsId=$SecondaryPls" `
  "primaryLocation=$($Lab.Location)" "secondaryLocation=$($Lab.SecondaryLocation)"
az network private-endpoint-connection list --id $PrimaryPls -o json
az network private-endpoint-connection list --id $SecondaryPls -o json
```

The manifest uses PLS visibility `*` so the Front Door managed endpoint can request a connection across subscription boundaries; **visibility is not approval or public network access**. Automatic approval is deliberately absent. Inspect the requester's endpoint IDs/subscription and compare with the new Front Door profile's pending requests. **Approve only those exact two**, never loop over arbitrary pending connections:

```powershell
$PrimaryConnectionId = Read-Host 'Verified Front Door pending connection ARM ID on primary PLS'
$SecondaryConnectionId = Read-Host 'Verified Front Door pending connection ARM ID on secondary PLS'
az network private-endpoint-connection approve --id $PrimaryConnectionId --description 'Reviewed Front Door lab origin'
az network private-endpoint-connection approve --id $SecondaryConnectionId --description 'Reviewed Front Door lab origin'
$EdgeHost = az deployment group show -g $Lab.ResourceGroup -n frontdoor --query properties.outputs.endpointHost.value -o tsv
$EdgeUrl = "https://$EdgeHost"
Invoke-WebRequest "$EdgeUrl/readyz"
Invoke-WebRequest "$EdgeUrl/readyz" -Headers @{'X-Lab-Waf-Test'='block'} -SkipHttpErrorCheck
```

Expected primary normal response 200 and WAF test 403; save WAF log showing `ControlledWafTest`. Managed default/bot rules and custom test rule run in Prevention. Secondary origin is **Disabled** in ARM until the data activation gate. Front Door probes only readiness, not database writeability or queue recovery.

**Review alternate entry points:** lab 3's `Gateway/gateway-system/orders-gateway` uses the `approuting-istio` class and an **internal** load balancer; its `HTTPRoute/orders/orders` is not a public-origin solution. This capstone creates a separate supported ILB/PLS origin rather than turning that Gateway public or assuming it supports Front Door Private Link directly. No public/DNAT variant is required for this path.

If Front Door must be the only supported application entry point, retire the old direct internal Gateway route from its authoritative platform source. If lab 3's objects are still manually platform-owned, remove these two named resources explicitly. Otherwise remove them through their Flux source, and ensure no controller recreates them. Retain the namespace/certificate until final cleanup:

```powershell
# Only if lab 3's Gateway/HTTPRoute are still manual and not subsequently Git-owned:
kubectl --context $PrimaryContext delete httproute orders -n orders
kubectl --context $PrimaryContext delete gateway orders-gateway -n gateway-system
kubectl --context $PrimaryContext get svc -A
kubectl --context $PrimaryContext get gateway,httproute -A
```

If direct internal management access is deliberately retained instead, record it as a trusted-network bypass of Front Door WAF—not as WAF-protected traffic. Check external direct-origin requests fail even with forged Host/`X-Azure-FDID`. PLS is not publicly routable; only the approved private endpoint connects Front Door. Internal VNet callers remain in the trusted network boundary—apply NSG/network policy constraints if they must also be forbidden. Do not use FDID header filtering alone as authentication. A future public/DNAT origin alternative would require an explicitly supported public listener, valid TLS, `AzureFrontDoor.Backend` source restrictions **and** validation of the exact Front Door profile ID; simply assigning a DNS name to this internal Gateway would not work.

When every origin is unhealthy, Front Door may route rather than become an absolute traffic fence: **Disabled origins and stopped writers are the incident controls**, not probe failure alone.

## 7. Execute a supported staged Fleet update independently of DR

```powershell
az extension add -n fleet --upgrade
az extension show -n fleet --query version
az fleet create -g $Lab.ResourceGroup -n $Fleet -l $Lab.Location --enable-managed-identity
az fleet member create -g $Lab.ResourceGroup --fleet-name $Fleet -n primary `
  --member-cluster-id $Primary.clusterId.value --update-group primary
az fleet member create -g $Lab.ResourceGroup --fleet-name $Fleet -n secondary `
  --member-cluster-id $Secondary.clusterId.value --update-group secondary
$Run = 'images-' + (Get-Date -Format yyyyMMddHHmm)
az fleet updaterun create -g $Lab.ResourceGroup --fleet-name $Fleet -n $Run `
  --upgrade-type NodeImageOnly --node-image-selection Latest --stages .\advanced\fleet-stages.json
az fleet updaterun start -g $Lab.ResourceGroup --fleet-name $Fleet -n $Run
az fleet updaterun show -g $Lab.ResourceGroup --fleet-name $Fleet -n $Run -o json
```

Use CLI ≥2.82 and fleet extension ≥1.8.3 per current quickstart. No hub is needed for update orchestration. The JSON uses GA stages/groups and a 600-second soak, **not preview maxAllowedFailures or approval features**. A timer is not a human approval gate.

Fleet respects member maintenance windows. Arrange overlapping approved windows and no competing cluster automatic upgrades before starting. While secondary stage executes, verify node versions, add-on health and image pull; during soak verify primary orders and stop the run on failure:

```powershell
az fleet updaterun stop -g $Lab.ResourceGroup --fleet-name $Fleet -n $Run
# Only run stop on an actual failed validation; an in-progress node operation may still finish.
```

For a successful exercise do not stop; wait until both member statuses are successful and save their image IDs. If already latest, record no-op and repeat after an available image release, or select a supported common Kubernetes target and use `Full --kubernetes-version <target>`. A no-op is not an actual image replacement. Fleet resources in the primary region are not part of the emergency traffic/data failover path.

## 8. Create pending business work, fence primary, and declare the incident

Do not run this during Fleet/AKS maintenance. First record a durable lab-8 order that already exists in the replica. Then deliberately accumulate a bounded Service Bus backlog.

Identify **all** Flux owners of the app, HPA and KEDA from lab 4–6. Suspend those named Kustomizations explicitly and record them; leave security/platform owners active:

```powershell
flux get kustomizations --context $PrimaryContext -A
kubectl --context $PrimaryContext get scaledobjects,hpa -n orders
$IncidentOwners = @('orders') # Lab 6 puts HPA/KEDA specifications under this same child.
$IncidentOwners | Set-Content .\.artifacts\advanced\incident-owners.txt
foreach ($Owner in $IncidentOwners) { flux suspend kustomization $Owner --context $PrimaryContext -n $FluxNamespace }
# Pause every worker ScaledObject in this application namespace before scaling its target.
kubectl --context $PrimaryContext annotate scaledobject --all -n orders autoscaling.keda.sh/paused-replicas=0 --overwrite
kubectl --context $PrimaryContext scale deployment order-worker -n orders --replicas=0
kubectl --context $PrimaryContext get pods -n orders
```

Wait for workers zero; KEDA's own controller must observe the pause. Do not delete the queue. If autoscaler APIs were not installed, skip the ScaledObject command only after confirming there is no worker autoscaler.

```powershell
$Batch = 1..20 | ForEach-Object { @{id="dr-$([guid]::NewGuid().ToString('N'))";item="widget-$_"} }
$Accepted = [System.Collections.Generic.List[object]]::new()
foreach ($Order in $Batch) {
  Invoke-RestMethod "$EdgeUrl/orders" -Method Post -ContentType application/json -Body ($Order | ConvertTo-Json)
  $Accepted.Add($Order)
}
$Accepted | ConvertTo-Json | Set-Content .\.artifacts\advanced\dr-accepted.json
az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName -n orders --query countDetails
$IncidentStart = [DateTime]::UtcNow
$IncidentStart.ToString('o') | Set-Content .\.artifacts\advanced\incident-start.txt
# ARM traffic fence, remove the API scale controller, then stop primary API.
az afd origin update -g $Lab.ResourceGroup --profile-name $FrontDoor --origin-group-name orders --origin-name primary --enabled-state Disabled
# orders is suspended; the durable Git fence below also removes this HPA from the effective render.
kubectl --context $PrimaryContext delete hpa order-api -n orders --ignore-not-found
kubectl --context $PrimaryContext scale deployment order-api -n orders --replicas=0
kubectl --context $PrimaryContext get hpa -n orders
kubectl --context $PrimaryContext get deploy,pods -n orders
```

Expected backlog ≥20 active messages, both primary workloads stopped, secondary app still zero, both edge origins disabled. Confirm no test producers remain. This models **regional application unavailability plus deliberate service promotions**. It does not destroy Azure networking or claim to reproduce a real regional platform outage.

Fencing must be durable before data promotion: update the **primary Git desired state** to API/worker zero and pause/disable its autoscalers, commit/push, while keeping its owners suspended until the recovery authority approves. Otherwise a restarted Flux controller could recreate old writers on recovery. Record the fencing commit SHA.

```powershell
.\ops\Add-GitOpsFile.ps1 -Source .\advanced\primary-fence.yaml -Kind Patch -Namespace orders
git add .\gitops\clusters\primary\apps\orders
.\advanced\Publish-ReviewedChange.ps1 -Message "Fence primary app and KEDA before regional promotion"
git rev-parse HEAD
```

The supplied patch targets lab 6's `ScaledObject/order-worker` and removes `HorizontalPodAutoscaler/order-api` from the effective Git render with a strategic `$patch: delete`. If a customer renamed either object, update those exact targets before rendering. Require the rendered API HPA to be absent, the worker ScaledObject paused at zero, both deployments at zero, and no live API HPA before promotion. Do not rely on HPA's zero-replica behavior as a durable writer fence. Do not remove the fencing patch while secondary is active.

## 9. Promote data and messages, then admit secondary application traffic

Because source services remain accessible, use **planned** operations. Do not force loss for this required path:

```powershell
az servicebus namespace failover -g $Lab.ResourceGroup -n $Lab.ServiceBusName `
  --primary-location $Lab.SecondaryLocation --force false
az servicebus namespace show -g $Lab.ResourceGroup -n $Lab.ServiceBusName --query geoDataReplication
az postgres flexible-server replica promote -g $SecondaryRg -n $PgReplica `
  --promote-mode standalone --promote-option planned --yes
az postgres flexible-server show -g $SecondaryRg -n $PgReplica --query '{state:state,role:replicationRole,host:fullyQualifiedDomainName}'
```

PostgreSQL standalone promotion deliberately leaves the old primary as a separate server. It does **not** automatically demote/fence the old server. The primary app is zero in Git and suspended, and edge primary remains Disabled. Before secondary enablement, use secondary `psql` to require `pg_is_in_recovery() = false`, presence of the previously durable marker, correct SQL grants and TLS. Verify Service Bus reports the selected primary region and active backlog via the same stable FQDN/local PE.

Edit only `advanced\regions\secondary\kustomization.yaml` replica counts: API 2 and worker 1. Keep its DB host pointing to the now promoted `$PgReplica`. Commit/push/reconcile:

```powershell
.\advanced\Set-RegionalReplicas.ps1 -Api 2 -Worker 1
kubectl kustomize .\advanced\regions\secondary | Select-String 'replicas:'
git add .\advanced\regions\secondary
.\advanced\Publish-ReviewedChange.ps1 -Message "Activate secondary after planned data and message promotion"
flux reconcile kustomization orders-secondary --context $SecondaryContext -n flux-system --with-source
kubectl --context $SecondaryContext rollout status deployment/order-api -n orders --timeout=600s
kubectl --context $SecondaryContext rollout status deployment/order-worker -n orders --timeout=600s
kubectl --context $SecondaryContext logs deployment/order-worker -n orders --tail=100
```

Wait for backlog to drain and inspect database rows **before** enabling edge traffic. Use private management access/port-forward for the business check if necessary; don't create a public Service:

```powershell
# Separate private-host terminal; leave this attached only for the verification period:
kubectl --context $SecondaryContext port-forward -n orders service/order-api 18080:80
# In another terminal on the same host:
$Accepted = Get-Content .\.artifacts\advanced\dr-accepted.json -Raw | ConvertFrom-Json
foreach ($Order in $Accepted) { Invoke-RestMethod "http://127.0.0.1:18080/orders/$($Order.id)" }
.\advanced\Test-OrderLedger.ps1 -BaseUri http://127.0.0.1:18080 -TimeoutSeconds 180
```

Require all 20 IDs and expected items, and one SQL row per ID. Then:

```powershell
az afd origin update -g $Lab.ResourceGroup --profile-name $FrontDoor --origin-group-name orders --origin-name secondary --enabled-state Enabled
Invoke-WebRequest "$EdgeUrl/readyz"
foreach ($Order in $Accepted) { Invoke-RestMethod "$EdgeUrl/orders/$($Order.id)" }
.\advanced\Test-OrderLedger.ps1 -BaseUri $EdgeUrl -TimeoutSeconds 180
$Recovered = [DateTime]::UtcNow
[pscustomobject]@{RtoSeconds=($Recovered-$IncidentStart).TotalSeconds;Accepted=$Accepted.Count;Lost=0}
```

Write `Lost=0` **only after verifying every recorded ID/item**. Re-submit one identical accepted order, confirm a single database row, then create a new order through Front Door and verify worker/database completion. Check dead-letter count is unchanged. Record HTTP recovery time separately from "all accepted orders processed" time. Missing IDs are an investigation, not permission to report success.

**Real inaccessible-region variant (discussion, not a required destructive action):** incident command weighs lag/data loss and fencing confidence before `--force true` for Service Bus or `--promote-option forced` for PostgreSQL. A network partition is not proof the old writer is dead. Microsoft recommends deleting/recreating the old Service Bus region after forced promotion rather than trusting resynchronization. Reconcile accepted-but-missing messages from an independent durable producer ledger/outbox; the lab's local evidence file is **not** a production recovery system.

## 10. Fail back with a new replica, not by pointing at stale data

Do not re-enable primary origin just because `/readyz` becomes healthy. After standalone promotion, the old `$Pg` is stale. While secondary remains active, create a **new** PostgreSQL replica in the primary region from the promoted secondary:

```powershell
$PgReturn = "$($Lab.Prefix)-pg-return"
$Promoted = az postgres flexible-server show -g $SecondaryRg -n $PgReplica -o json | ConvertFrom-Json
az postgres flexible-server replica create -g $Lab.ResourceGroup -n $PgReturn `
  --source-server $Promoted.id --location $Lab.Location
$ReturnServer = az postgres flexible-server show -g $Lab.ResourceGroup -n $PgReturn -o json | ConvertFrom-Json
az postgres flexible-server update -g $Lab.ResourceGroup -n $PgReturn --public-access Disabled
.\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $Lab.ResourceGroup -Location $Lab.Location `
  -Name "$($Lab.Prefix)-pg-return-pe" -ResourceId $ReturnServer.id -GroupId postgresqlServer `
  -SubnetId $Primary.endpointsSubnetId.value -VnetId $Primary.vnetId.value -ZoneName privatelink.postgres.database.azure.com
az postgres flexible-server microsoft-entra-admin create -g $Lab.ResourceGroup -s $PgReturn --object-id $Admin.id --display-name $Admin.userPrincipalName --type User
```

Wait for healthy replication and a secondary-created order visible on the return replica. Verify primary roles `orders_api` / `orders_worker` still map to the original identities. Schedule a short write outage for the planned return:

1. Disable the secondary Front Door origin; stop all producers.
2. Wait for secondary queue active count zero and in-flight processing settled. Require every accepted ID present in SQL.
3. Change secondary source replica counts to zero, commit/push/reconcile; verify no secondary worker/API pods.
4. Promote the return replica **planned**, then promote Service Bus back **planned**.

```powershell
az afd origin update -g $Lab.ResourceGroup --profile-name $FrontDoor --origin-group-name orders --origin-name secondary --enabled-state Disabled
# Wait for zero backlog and settled in-flight work before fencing compute:
az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName -n orders --query countDetails
.\advanced\Set-RegionalReplicas.ps1 -Api 0 -Worker 0
git add .\advanced\regions\secondary
.\advanced\Publish-ReviewedChange.ps1 -Message "Fence secondary for planned failback"
flux reconcile kustomization orders-secondary --context $SecondaryContext -n flux-system --with-source
kubectl --context $SecondaryContext get deployment,pods -n orders
# Continue only when no secondary API/worker pods remain:
az postgres flexible-server replica promote -g $Lab.ResourceGroup -n $PgReturn `
  --promote-mode standalone --promote-option planned --yes
az servicebus namespace failover -g $Lab.ResourceGroup -n $Lab.ServiceBusName `
  --primary-location $Lab.Location --force false
```

Update the authoritative **primary** app `POSTGRES_HOST` to `$ReturnServer.fullyQualifiedDomainName`, update the TCP 5432 PE IP allowlist, and retain `orders_api`/`orders_worker`. Removing the temporary fencing patch must restore `HorizontalPodAutoscaler/order-api` from its unchanged source resource and **leave Deployment replicas omitted from normal primary Git source**, as established in lab 6: HPA owns API scale and KEDA owns worker activation. Preserve their existing specifications. Remove the incident KEDA pause from live resources **and source**, resume only the recorded `orders` child and reconcile:

```powershell
$ReturnIp = (Resolve-DnsName $ReturnServer.fullyQualifiedDomainName -Type A | Where-Object IPAddress | Select-Object -First 1).IPAddress
.\advanced\Set-PrimaryDatabase.ps1 -HostName $ReturnServer.fullyQualifiedDomainName -PrivateIp $ReturnIp
.\ops\Remove-GitOpsFile.ps1 -Name primary-fence.yaml -Namespace orders
git add .\gitops\clusters\primary\apps\orders
.\advanced\Publish-ReviewedChange.ps1 -Message "Return primary to the recovered database and remove writer fence"
kubectl --context $PrimaryContext annotate scaledobject --all -n orders autoscaling.keda.sh/paused-replicas-
foreach ($Owner in $IncidentOwners) {
  flux resume kustomization $Owner --context $PrimaryContext -n $FluxNamespace
  flux reconcile kustomization $Owner --context $PrimaryContext -n $FluxNamespace --with-source
}
kubectl --context $PrimaryContext get hpa order-api -n orders
# Explicit one-time failback activation, not a permanent replica value in Git.
# HPA does not awaken an API deliberately left at zero; do not assume removing a patch does so.
kubectl --context $PrimaryContext scale deployment order-api -n orders --replicas=2
kubectl --context $PrimaryContext scale deployment order-worker -n orders --replicas=1
kubectl --context $PrimaryContext rollout status deployment/order-api -n orders --timeout=600s
kubectl --context $PrimaryContext rollout status deployment/order-worker -n orders --timeout=600s
```

The two scale commands are recorded operational activation seeds after the writer-ownership handover; subsequent scale belongs to HPA/KEDA, not Flux. Require no `spec.replicas` or replica transformer in the normal primary source after fence removal. If source still includes zero replicas, simply removing a KEDA pause is not a durable repair; inspect desired objects and scaled-object triggers. KEDA may legitimately return to zero after draining. Verify database writeability, the entire accepted ledger and a new primary order over the private management path before enabling primary origin:

```powershell
az afd origin update -g $Lab.ResourceGroup --profile-name $FrontDoor --origin-group-name orders --origin-name primary --enabled-state Enabled
Invoke-WebRequest "$EdgeUrl/readyz"
foreach ($Order in $Accepted) { Invoke-RestMethod "$EdgeUrl/orders/$($Order.id)" }
.\advanced\Test-OrderLedger.ps1 -BaseUri $EdgeUrl -ReportPath .\.artifacts\advanced\failback-verification.json
kubectl --context $SecondaryContext get deployment -n orders
az servicebus namespace show -g $Lab.ResourceGroup -n $Lab.ServiceBusName --query geoDataReplication
```

Expected primary active, secondary API/worker zero, primary edge Enabled/secondary Disabled, queue promoted back and all orders present. **The active primary database is now `$PgReturn`, not `$Pg`.** Update operational configuration/inventory and future IaC strategy; do not rerun the old `postgres` deployment and silently point back to an empty or stale server. Re-establish a read replica from the new active database to restore protection after the exercise; standalone promotions break the former replication relationship.

## 11. Deliver the customer decision and safely decommission

Deliver a UTC timeline, infrastructure/output IDs, Git SHAs, certificate validation/private-origin evidence, WAF block evidence, Fleet member outcomes, both promotions, all accepted IDs/items, duplicate test, replication lag observations, measured RTO/RPO, failback result and new authoritative database name.

**Model answers**

1. **"Why not active/active?"** Competing writers require conflict semantics, ownership and distributed-state design. This service has one active worker region and an explicit write-fencing protocol; a routing weight is not a concurrency control.
2. **"Why not automatic failover whenever a probe fails?"** A readiness failure does not identify database correctness, queue replication or whether the original writer is alive. Operator/data gates avoid split brain; automation must encode and verify those gates.
3. **"Does synchronous Service Bus give zero-loss orders?"** It improves message replication semantics, but database replication is separate. A completed message whose row was not replicated can still cause a cross-system gap in a real forced event. Use a durable outbox/inbox/reconciliation design for stronger guarantees.
4. **"Why Fleet?"** It provides repeatable update sequencing and status across members. It is neither a global ingress controller for this application nor a database/message recovery engine.
5. **"When is this worth it?"** When quantified business loss and recovery requirements justify two-region capacity, replication latency/cost, certificates, DNS, security operations, on-call authority and repeated rehearsals. Zone redundancy plus restore may be adequate for less stringent requirements.

Do **not** delete the secondary while the active database or Service Bus primary still resides there. At end-of-pack teardown:

1. Confirm successful failback or obtain explicit approval to delete all synthetic business data. Stop producers; disable both Front Door origins.
2. Stop active Fleet runs; remove member registrations (`az fleet member delete -g $Lab.ResourceGroup --fleet-name $Fleet -n secondary --yes`, likewise primary), then delete the Fleet resource.
3. Remove regional app/origin Kustomizations through their Git bootstrap sources and prune **before** deleting clusters so AKS cleans up PLS/load balancers. Delete Front Door/security policy/WAF after traffic is intentionally retired; revoke private endpoint connections.
4. Remove Service Bus secondary replication location only after zero backlog and verified promotion state; use `namespace update --locations` with a reviewed single Primary entry. This deletes the removed region's replica data. If finishing everything, delete the namespace only as part of final foundation teardown.
5. Keep active `$PgReturn` and any required replica/backup until retention expires. Delete stale `$Pg` / promoted `$PgReplica` only after checking no workload endpoint references them. Remove their PEs, not shared private DNS zone links still serving other databases.
6. Remove ACR geo-replication only after all secondary digest consumers are gone; do not delete shared ACR while primary still runs. Delete secondary scoped identities/role assignments, private endpoints and the secondary RG last (`az group delete -n $SecondaryRg --yes`) only after enumerating its resources.
7. Follow lab 8's backup retention/extension/snapshot teardown **before** removing the primary cluster. Preserve the evidence bundle; securely delete local TLS private keys and auth material through the approved workstation process. No subscription-wide policy/Defender state is blindly reset.

## Official references and status

Checked **2026-09-10**: [Service Bus Geo-Replication](https://learn.microsoft.com/azure/service-bus-messaging/service-bus-geo-replication) (non-partitioned Premium required; partitioned support preview), [GA namespace CLI](https://learn.microsoft.com/cli/azure/servicebus/namespace), [GA PostgreSQL replica CLI](https://learn.microsoft.com/cli/azure/postgres/flexible-server/replica), [Front Door private ILB origin](https://learn.microsoft.com/azure/frontdoor/standard-premium/how-to-enable-private-link-internal-load-balancer), [origin security](https://learn.microsoft.com/azure/frontdoor/origin-security), [AKS ILB/PLS restrictions](https://learn.microsoft.com/azure/aks/internal-lb), [Fleet quickstart](https://learn.microsoft.com/azure/kubernetes-fleet/quickstart-create-fleet-and-members), [Fleet update orchestration](https://learn.microsoft.com/azure/kubernetes-fleet/update-orchestration). Some Service Bus documentation examples use preview ARM schemas; this required path uses current **GA core CLI** commands and excludes partitioned preview replication. Front Door origin managed-identity authentication and Fleet preview failure tolerances are not prerequisites.
