# Lab 8 — Recover the state, not just the pods

**Customer:** durable order processing, shared files and a tested recovery plan. **Time:** 4–6 hours plus provisioning/backup time. **Result:** passwordless PostgreSQL, Disk/Files CSI evidence, an actual AKS Backup restore with a matching hash, and a separate PostgreSQL point-in-time restore.

Required after labs 1–7. Budget for PostgreSQL General Purpose, storage, backup snapshots and restore servers. Run from the root on a private management host. Install PostgreSQL **17 or later client tools** (`psql`, `pg_dump`, `pg_restore`) on that host through your approved package process; `sslrootcert=system` requires a recent libpq. No PostgreSQL password or Azure token belongs in Git or a transcript.

## 1. Inspect the exact recovery support boundary

```powershell
. .\scripts\Use-Lab.ps1
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
New-Item .\.artifacts\advanced -ItemType Directory -Force | Out-Null
$Out = az deployment group show -g $Lab.ResourceGroup -n foundation --query properties.outputs -o json | ConvertFrom-Json
$AppKustomization = 'orders'
$GitSource = 'flux-system'
$FluxNamespace = 'flux-system'
$Pg = "$($Lab.Prefix)-pg"
$PgRestore = "$($Lab.Prefix)-pg-pitr"
$Admin = az ad signed-in-user show -o json | ConvertFrom-Json
Get-Command psql,pg_dump,pg_restore
az postgres flexible-server list-skus -l $Lab.Location -o table
az aks show -g $Lab.ResourceGroup -n $Lab.ClusterName --query '{version:kubernetesVersion,storage:storageProfile,identity:identity}'
kubectl get csidrivers
```

Use a human Entra PostgreSQL administrator for initialization, not the app's managed identity. Needed permissions: resource creation, private DNS/network writes, role-assignment writes, AKS platform administration, and Backup Contributor. The backup role-preparation helper assigns documented roles on the dedicated snapshot group; inspect the resulting scope instead of granting subscription Owner.

**Support gate (checked 2026-09-10):** GA Disk CSI operational backups are the required path. Source cluster, vault, extension StorageV2 account and snapshots reside in the same region. The backup extension requires an x86 Linux pool and a supported AKS version. The baseline private API plus firewall is **not** the separate "Network Isolated AKS" feature (which the support matrix excludes). Do not install Velero alongside the backup extension.

Azure Files SMB backup is currently documented but **private-endpoint Azure Files is unsupported by AKS Backup**. Therefore Files in this lab is private, in `files-lab`, and deliberately **outside** the protected `storage-lab` namespace. NFS is not a substitute. Do not make customer file shares public to satisfy a backup exercise. Only Disk volumes support Vault-tier recovery; Operational snapshots alone are not regional DR.

## 2. Create and privately resolve managed PostgreSQL

Review the Bicep: PostgreSQL 16, General Purpose, Entra-only auth, 14-day retention, no public access. HA is disabled to bound lab cost; discuss zone-redundant HA for production separately. Geo-redundant backup is disabled because lab 10 demonstrates an explicitly provisioned cross-region replica, not an imaginary geo-restore.

```powershell
az deployment group create -g $Lab.ResourceGroup -n postgres -f .\advanced\postgres.bicep `
  -p "serverName=$Pg" "location=$($Lab.Location)" "adminObjectId=$($Admin.id)" "adminLogin=$($Admin.userPrincipalName)"
$PgOut = az deployment group show -g $Lab.ResourceGroup -n postgres --query properties.outputs -o json | ConvertFrom-Json
.\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $Lab.ResourceGroup -Location $Lab.Location `
  -Name "$($Lab.Prefix)-pg-pe" -ResourceId $PgOut.serverId.value -GroupId postgresqlServer `
  -SubnetId $Out.endpointsSubnetId.value -VnetId $Out.vnetId.value -ZoneName privatelink.postgres.database.azure.com
$PgHost = $PgOut.hostname.value
Resolve-DnsName $PgHost
Test-NetConnection $PgHost -Port 5432
```

Expected: a private address, TCP 5432 reachable from the management host, and public network access Disabled. This is **Private Link networking**, not the mutually exclusive delegated-subnet VNet-integration model. Allow TCP 5432 to this PE address in the application's egress NetworkPolicy; also preserve DNS, Entra HTTPS, Service Bus and Key Vault allowances from lab 3. Modify the **Git source** policy, not live workloads.

## 3. Bind identities and enable durable application behavior through Git

```powershell
.\advanced\Initialize-OrdersDatabase.ps1 -HostName $PgHost -AdminLogin $Admin.userPrincipalName `
  -ApiPrincipalId $Out.apiPrincipalId.value -WorkerPrincipalId $Out.workerPrincipalId.value
```

The script creates database principals `orders_api` and `orders_worker` using their **object/principal IDs**, not client IDs. It creates:

```sql
processed_orders(order_id text PRIMARY KEY, item text NOT NULL,
                 processed_at timestamptz DEFAULT now())
```

API gets SELECT; worker gets INSERT and SELECT. The worker's `INSERT ... ON CONFLICT DO NOTHING` makes repeated deliveries of an **identical ID and item** idempotent, then it completes the Service Bus message. This is not global exactly-once delivery. Reusing an ID with a different item is explicitly rejected by this application's worker and sent to the dead-letter queue; it does not update the stored item.

The lab-4 production source is `gitops\clusters\primary\apps\orders` and its reconciler is `orders` in `flux-system`. Generate and add a database patch plus a private-IP egress policy using the existing lab-4 helper:

```powershell
$PgIp = (Resolve-DnsName $PgHost -Type A | Where-Object IPAddress | Select-Object -First 1).IPAddress
.\advanced\Set-PrimaryDatabase.ps1 -HostName $PgHost -PrivateIp $PgIp
```

This adds the following values to the production source deployments' `env` lists (without replacing existing identity/Service Bus/ROLE values):

```yaml
# order-api
- name: POSTGRES_HOST
  value: "<the value of $PgHost>"
- name: POSTGRES_DATABASE
  value: ordersdb
- name: POSTGRES_USER
  value: orders_api
- name: POSTGRES_PORT
  value: "5432"
# order-worker: same host/database/port, POSTGRES_USER = orders_worker
```

`DefaultAzureCredential` uses each pod's Workload ID and requests `https://ossrdbms-aad.database.windows.net/.default`; it refreshes tokens when opening connections. The application verifies PostgreSQL TLS with `sslmode=verify-full` and `/etc/ssl/certs/ca-certificates.crt` inside the Linux container; retain the image's CA bundle and use the server FQDN, not a private IP, for `POSTGRES_HOST`. No `POSTGRES_PASSWORD` secret is added. Ensure both images include the PostgreSQL integration from the shared app contract.

```powershell
git diff
git add .\gitops\clusters\primary\apps\orders
.\advanced\Publish-ReviewedChange.ps1 -Message "Use private passwordless durable order storage"
# Use the exact Flux names recorded in lab 7.
flux reconcile kustomization $AppKustomization -n $FluxNamespace --with-source
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl rollout status deployment/order-worker -n orders --timeout=300s
# KEDA may keep an empty-queue worker at zero; submit an order before requiring worker logs.
```

If the lab-4 source lives outside `k8s`, stage its actual path instead. Expected: no token acquisition/permission errors. Diagnose in order: federation → DNS/route → TCP/TLS → Entra principal mapping → SQL grants. The Azure PostgreSQL resource Contributor role does **not** grant SQL SELECT.

Post an order through the lab-3 TLS application URL, wait for durable processing, restart the worker and verify it still exists:

```powershell
$AppUrl = Read-Host 'Lab 3 HTTPS application base URL, no trailing slash'
$Order = @{id="durable-$([guid]::NewGuid().ToString('N'))";item='blue-widget'}
$Order | ConvertTo-Json | Set-Content .\.artifacts\advanced\durable-order.json
Invoke-RestMethod "$AppUrl/orders" -Method Post -ContentType application/json -Body ($Order | ConvertTo-Json)
# Repeat GET until processed, with a bounded wait.
$Found = $false
1..30 | ForEach-Object {
  if (-not $Found) {
    try { $Result = Invoke-RestMethod "$AppUrl/orders/$($Order.id)"; $Found = $true }
    catch [Microsoft.PowerShell.Commands.HttpResponseException] {
      if ([int]$_.Exception.Response.StatusCode -ne 404) { throw }
      Start-Sleep 2
    }
  }
}
if (-not $Found) { throw 'Order did not become durable within 60 seconds.' }
kubectl rollout restart deployment/order-worker -n orders
kubectl rollout status deployment/order-worker -n orders --timeout=300s
Invoke-RestMethod "$AppUrl/orders" -Method Post -ContentType application/json -Body ($Order | ConvertTo-Json)
Invoke-RestMethod "$AppUrl/orders/$($Order.id)"
```

A rollout restart is an explicit, recorded operations action; it does not replace the Git deployment definition. Confirm SQL count for this ID is exactly one using section 7's authenticated `psql` environment. The pre-lab-8 in-memory deduplication was not durable.

Now exercise the conflicting-ID business rule without mistaking HTTP acceptance for processing:

```powershell
$DeadLetterBefore = [long](az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName -n orders --query countDetails.deadLetterMessageCount -o tsv)
$Conflict = @{id=$Order.id;item='conflicting-red-widget'}
Invoke-WebRequest "$AppUrl/orders" -Method Post -ContentType application/json -Body ($Conflict | ConvertTo-Json)
# The POST returns 202 because enqueue succeeded; rejection happens asynchronously.
$DeadLetterAfter = $DeadLetterBefore
1..30 | ForEach-Object {
  if ($DeadLetterAfter -le $DeadLetterBefore) {
    Start-Sleep 2
    $DeadLetterAfter = [long](az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName -n orders --query countDetails.deadLetterMessageCount -o tsv)
  }
}
if ($DeadLetterAfter -le $DeadLetterBefore) { throw 'Expected conflicting order to be dead-lettered; inspect the worker.' }
$Unchanged = Invoke-RestMethod "$AppUrl/orders/$($Order.id)"
if ($Unchanged.item -cne $Order.item) { throw 'Conflicting delivery changed the stored order.' }
kubectl logs -n orders deployment/order-worker --tail=100
```

Expected: worker evidence of `Order ID already exists with a different item`, an `InvalidOrder` dead-letter reason, unchanged original data, and one SQL row for the ID. Perform this while other synthetic producers are stopped so the count increase is attributable; inspect the specific message with lab 6's dead-letter tooling. Preserve that known dead-letter message as evidence and record the new baseline for lab 10; do not blindly replay it. Before database integration, GET returned 503 by design, not an ingress failure. After integration, POST 202 still means **accepted into the queue**, not durably processed.

## 4. Compare Disk and Files mechanics

Commit and bootstrap a **platform** Kustomization for `advanced\storage`:

```powershell
git add .\advanced\storage
.\advanced\Publish-ReviewedChange.ps1 -Message "Add CSI storage recovery exercise"
flux create kustomization storage -n $FluxNamespace --source "GitRepository/$GitSource" --path ./advanced/storage --prune --interval 1m
flux reconcile kustomization storage -n $FluxNamespace --with-source
kubectl rollout status deployment/ledger -n storage-lab --timeout=600s
kubectl get pvc -n storage-lab
kubectl get pv -o wide
kubectl exec -n storage-lab deployment/ledger -- sh -c 'printf "order-001,blue-widget\norder-002,green-widget\n" > /data/ledger.csv; sync; sha256sum /data/ledger.csv'
$ExpectedHash = (kubectl exec -n storage-lab deployment/ledger -- sha256sum /data/ledger.csv).Split(' ')[0]
$ExpectedHash | Set-Content .\.artifacts\advanced\ledger.sha256
```

Persist `storage` using lab 7's export-and-register recipe, substituting `storage`/`storage.yaml` for `teams`/`teams.yaml`. Add the exported CR to `gitops\clusters\primary\kustomization.yaml`'s explicit resource list and commit/reconcile the platform root. The namespace and StorageClass need platform ownership; do not grant those rights to `orders-reconciler`.

Add `files.yaml` to `advanced\storage\kustomization.yaml`'s resources list, commit/push/reconcile `storage`. Dynamic private Files provisioning requires CSI identity permissions on the VNet/private DNS and allowed Azure control-plane egress. The core identity has network rights on the foundation VNet; if private DNS creation is denied, assign **Private DNS Zone Contributor on the required zone resource** and **Network Contributor on the subnet**, rather than opening storage to the internet. Inspect `kubectl describe pvc -n files-lab shared-files` and CSI controller logs for the exact failed resource.

```powershell
kubectl rollout status deployment/files-reader -n files-lab --timeout=600s
$Readers = (kubectl get pods -n files-lab -l app=files-reader -o json | ConvertFrom-Json).items.metadata.name
kubectl exec -n files-lab $Readers[0] -- sh -c 'printf "shared-evidence\n" > /data/shared.txt; sync'
kubectl exec -n files-lab $Readers[1] -- cat /data/shared.txt
```

Expected: both replicas read the same file. `ReadWriteOnce` is a **single-node** access mode, not a single-pod lock; `ReadWriteMany` supports multi-node writers but does not provide application locking. LRS Disk topology can bind to a zone; `WaitForFirstConsumer` avoids premature placement. `Delete` reclaim is deliberately dangerous and cheap for the synthetic exercise. Production may choose `Retain` with an explicit orphan-disk cleanup procedure.

**Controlled break/fix:** suspend `storage`, set an impossible node selector on the disk deployment, and inspect scheduling:

```powershell
flux suspend kustomization storage -n $FluxNamespace
kubectl patch deployment ledger -n storage-lab --type merge -p '{"spec":{"template":{"spec":{"nodeSelector":{"lab.example.com/absent":"true"}}}}}'
kubectl get pods -n storage-lab
kubectl describe pods -n storage-lab
```

Expected Pending/FailedScheduling and selector mismatch, **not** "lost disk data." Repair by resuming the owner:

```powershell
flux resume kustomization storage -n $FluxNamespace
flux reconcile kustomization storage -n $FluxNamespace
kubectl rollout status deployment/ledger -n storage-lab --timeout=600s
kubectl exec -n storage-lab deployment/ledger -- sha256sum /data/ledger.csv
```

Hash must still match. A real zone-mismatch incident similarly requires a schedulable node in the disk's zone or a supported data-copy/restore operation, not changing PV affinity to pretend the disk moved.

## 5. Configure AKS Backup completely

The helper creates a private StorageV2 metadata account/container, PE/DNS, Backup vault, operational policy, stable backup extension, blob data role, Trusted Access, dedicated snapshot RG, scoped backup config, managed-identity permissions and backup validation. Review the helper before running. It never disables storage firewalls or uses AKS admin credentials.

```powershell
az extension add -n dataprotection --upgrade
az extension add -n k8s-extension --upgrade
az extension show -n dataprotection --query version
.\advanced\Invoke-AksBackup.ps1 -Operation Configure
az k8s-extension show -n azure-aks-backup --cluster-type managedClusters --cluster-name $Lab.ClusterName -g $Lab.ResourceGroup
kubectl get pods -n dataprotection-microsoft
az aks trustedaccess rolebinding list -g $Lab.ResourceGroup --cluster-name $Lab.ClusterName
$Instance = Get-Content .\.artifacts\advanced\backup-created.json -Raw | ConvertFrom-Json
$Vault = "$($Lab.Prefix)-backup"
az dataprotection backup-instance show -g $Lab.ResourceGroup --vault-name $Vault -n $Instance.name
```

Wait for extension healthy and protection configured. If roles are newly assigned, allow propagation then repeat the helper's `validate-for-backup` and instance-create lines, not the entire provisioning sequence. A denied endpoint usually means missing private DNS or firewall egress; Microsoft documents required extension FQDNs in the backup concept article. Amend lab-3 firewall policy narrowly, retaining existing rules.

The default policy is obtained from the installed CLI, not hard-coded with an obsolete backup rule name. Verify its scope is **only storage-lab**. Backing up every namespace also copies Kubernetes secrets into metadata storage; protect and minimize that access. Private PostgreSQL and `files-lab` are not part of this backup.

## 6. Recover a destroyed file into a separate namespace

Quiesce writes: the ledger process only sleeps; ensure no `kubectl exec` writes during backup. `sync` is not a multi-volume database consistency protocol.

```powershell
.\advanced\Invoke-AksBackup.ps1 -Operation Backup
az dataprotection job list-from-resourcegraph --datasource-type AzureKubernetesService --datasource-id $Out.clusterId.value --operation OnDemandBackup -o json
az dataprotection recovery-point list -g $Lab.ResourceGroup --vault-name $Vault --backup-instance-name $Instance.name -o json
```

**Stop until the specific backup job is Completed and its recovery point exists.** Save the job ID, timestamps and recovery-point name. Do not pick "latest" before the requested backup has finished. The job should include the Disk PVC and have no skipped/failed protected volume.

```powershell
$RecoveryPointId = Read-Host 'Verified completed operational recovery point name'
kubectl exec -n storage-lab deployment/ledger -- sh -c 'rm /data/ledger.csv; sync'
# Controlled incident: source file is now absent.
$PSNativeCommandUseErrorActionPreference = $false
kubectl exec -n storage-lab deployment/ledger -- cat /data/ledger.csv
$PSNativeCommandUseErrorActionPreference = $true
.\advanced\Invoke-AksBackup.ps1 -Operation Restore -RecoveryPointId $RecoveryPointId
az dataprotection job list-from-resourcegraph --datasource-type AzureKubernetesService --datasource-id $Out.clusterId.value --operation Restore -o json
```

The request maps `storage-lab` to **storage-restored**, `RestoreWithVolumeData`, conflict policy Skip. Flux does not own the target namespace; do not add it to Git before restore. Target is the same supported cluster/region, so extension, Trusted Access and node capacity are already present. The helper updates restore roles and calls **validate-for-restore before triggering**.

Wait for restore job Completed, then:

```powershell
kubectl rollout status deployment/ledger -n storage-restored --timeout=600s
kubectl get pvc -n storage-restored
$ActualHash = (kubectl exec -n storage-restored deployment/ledger -- sha256sum /data/ledger.csv).Split(' ')[0]
if ($ActualHash -ne (Get-Content .\.artifacts\advanced\ledger.sha256).Trim()) { throw 'Restored file integrity failed.' }
kubectl exec -n storage-restored deployment/ledger -- cat /data/ledger.csv
```

**Full success is data + Kubernetes resource restore + application access**, not a successful ARM request. If the restored deployment is denied by PSA/Policy, compare restored resources against current admission rules; fix the isolated restored source manifest with an approved exception or compatible security context, never disable controls globally. If a backup/restore job reports partial success, investigate volume-level errors and repeat; do not count it as a pass.

## 7. Exercise PostgreSQL's independent point-in-time recovery

AKS Backup did not protect the managed database. First keep the order ID and expected item; enable `psql` access without storing a password:

```powershell
$env:PGHOST = $PgHost
$env:PGUSER = $Admin.userPrincipalName
$env:PGDATABASE = 'ordersdb'
$env:PGSSLMODE = 'verify-full'
$env:PGSSLROOTCERT = 'system'
$env:PGPASSWORD = az account get-access-token --resource https://ossrdbms-aad.database.windows.net --query accessToken -o tsv
try {
  psql -X --set ON_ERROR_STOP=1 -c 'SELECT order_id,item,processed_at FROM processed_orders ORDER BY order_id;'
  $RestoreTime = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
  $RestoreTime | Set-Content .\.artifacts\advanced\postgres-restore-time.txt
  Start-Sleep 60
  # Synthetic-only incident: database deletion, not a deployment failure.
  psql -X --set ON_ERROR_STOP=1 -c 'DELETE FROM processed_orders;'
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
```

Stop new producers during the short incident; leave the live API on the original database until the evidence is captured. Wait until the server's restore window includes `$RestoreTime`:

```powershell
az postgres flexible-server show -g $Lab.ResourceGroup -n $Pg --query '{state:state,backup:backup}'
az postgres flexible-server restore -g $Lab.ResourceGroup -n $PgRestore --source-server $PgOut.serverId.value --restore-time $RestoreTime
$RestoredPg = az postgres flexible-server show -g $Lab.ResourceGroup -n $PgRestore -o json | ConvertFrom-Json
az postgres flexible-server update -g $Lab.ResourceGroup -n $PgRestore --public-access Disabled
.\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $Lab.ResourceGroup -Location $Lab.Location `
  -Name "$($Lab.Prefix)-pitr-pe" -ResourceId $RestoredPg.id -GroupId postgresqlServer `
  -SubnetId $Out.endpointsSubnetId.value -VnetId $Out.vnetId.value -ZoneName privatelink.postgres.database.azure.com
az postgres flexible-server microsoft-entra-admin create -g $Lab.ResourceGroup -s $PgRestore --object-id $Admin.id --display-name $Admin.userPrincipalName --type User
```

If the administrator already exists, inspect it rather than duplicating it. A restore creates a **new server**, not an in-place undo. PE, role assignments, DNS and server configuration need verification; do not assume restored SQL roles alone reproduce Azure control-plane settings.

```powershell
$env:PGHOST = $RestoredPg.fullyQualifiedDomainName
$env:PGPASSWORD = az account get-access-token --resource https://ossrdbms-aad.database.windows.net --query accessToken -o tsv
try {
  psql -X --set ON_ERROR_STOP=1 -c 'SELECT order_id,item,processed_at FROM processed_orders ORDER BY order_id;'
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
```

Expected durable order reappears with its original item. Record actual recovery duration and the gap between restored time and incident time. For this lab, **do not repoint production**: preserve original `$Pg` for lab 10. Re-submit the saved synthetic order through the application to repopulate the original server, then verify GET and SQL again. In a real cutover, fence all writers, compare records, update the authoritative Git endpoint and recycle connections; never run two independent writable databases unintentionally.

## 8. Debrief and clean up only disposable recovery resources

Deliver: SQL principal grants, durable dedup count after restart, private DNS/TLS evidence, Disk/Files mode comparison, failed scheduling event, completed backup/restore job IDs, matching hash, PITR restored rows, measured recovery duration and known missing state.

**Model answers**

1. **"Can Git restore the system?"** It can reconstruct desired resources, not orders, queue acknowledgements, external databases or file contents.
2. **"Are snapshots consistent?"** These snapshots are crash-consistent. A database may require transaction-log replay or coordinated quiescing; independent volume snapshots are not a distributed transaction.
3. **"Why managed PostgreSQL?"** Managed backups, patching and HA reduce toil, but schema changes, SQL privilege design, connection management, capacity and recovery validation remain ours. Kubernetes hosting is justified only with explicit operational ownership and requirements.
4. **"Are we protected from a region loss?"** Not by this local Disk exercise. Vault-tier Disk backups with GRS and Cross Region Restore can restore in the supported paired region; they need a prepared target, staging storage, permissions and validated recovery points. Private Files needs its own supported protection strategy. Lab 10 replicates the managed state separately.

Keep primary PostgreSQL, app SQL integration and backup protection for labs 9–10. Delete the disposable restore server only after retaining its evidence:

```powershell
kubectl delete namespace storage-restored
az network private-endpoint delete -g $Lab.ResourceGroup -n "$($Lab.Prefix)-pitr-pe"
az postgres flexible-server delete -g $Lab.ResourceGroup -n $PgRestore --yes
```

At final teardown: stop protection/delete backup data using `az dataprotection backup-instance delete -g $Lab.ResourceGroup --vault-name $Vault -n $Instance.name --yes`, wait for jobs/soft-delete/immutability requirements, then remove backup extension/Trusted Access, metadata storage/PE and snapshot RG **only after no recovery points depend on them**. Keep the extension and cluster running while operational backup expiry/deletion needs them. Remove `storage` resources through Git and prune; check orphan PVs/disks/shares before deleting their node RG. Never purge a shared backup vault or defeat retention just to make an RG delete succeed.

## Official references and status

Checked **2026-09-10**: [AKS Backup support matrix](https://learn.microsoft.com/azure/backup/azure-kubernetes-service-cluster-backup-support-matrix), [backup CLI](https://learn.microsoft.com/azure/backup/azure-kubernetes-service-cluster-backup-using-cli), [restore CLI](https://learn.microsoft.com/azure/backup/azure-kubernetes-service-cluster-restore-using-cli), [backup concepts/network requirements](https://learn.microsoft.com/azure/backup/azure-kubernetes-service-cluster-backup-concept), [PostgreSQL Bicep API](https://learn.microsoft.com/azure/templates/microsoft.dbforpostgresql/2024-08-01/flexibleservers), [PostgreSQL CLI](https://learn.microsoft.com/cli/azure/postgres/flexible-server). Some introductory paragraphs in the backup CLI article lag the support matrix; this lab uses the narrower supported Disk operational path and does not claim private Files backup support.
