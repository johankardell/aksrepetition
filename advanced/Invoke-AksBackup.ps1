[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Configure','Backup','Restore')][string]$Operation,
    [string]$RecoveryPointId
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
. .\scripts\Use-Lab.ps1
$out = az deployment group show -g $Lab.ResourceGroup -n foundation --query properties.outputs -o json | ConvertFrom-Json
$directory = '.\.artifacts\advanced'
New-Item $directory -ItemType Directory -Force | Out-Null
$vault = "$($Lab.Prefix)-backup"
$snapshotRg = "$($Lab.ResourceGroup)-snapshots"
$storagePrefix = $Lab.Prefix.Substring(0, [Math]::Min(10, $Lab.Prefix.Length))
$storage = ("${storagePrefix}backup" + $Lab.SubscriptionId.Replace('-','').Substring(0,8)).ToLower()
$vaultId = "/subscriptions/$($Lab.SubscriptionId)/resourceGroups/$($Lab.ResourceGroup)/providers/Microsoft.DataProtection/backupVaults/$vault"
$configPath = "$directory\backup-config.json"
$instancePath = "$directory\backup-instance.json"
if ($Operation -eq 'Configure') {
    foreach ($provider in 'Microsoft.DataProtection','Microsoft.KubernetesConfiguration') {
        az provider register --namespace $provider --wait
    }
    az group create -n $snapshotRg -l $Lab.Location -o none
    az storage account create -g $Lab.ResourceGroup -n $storage -l $Lab.Location --kind StorageV2 --sku Standard_LRS --min-tls-version TLS1_2 --public-network-access Disabled --allow-blob-public-access false -o none
    $storageId = az storage account show -g $Lab.ResourceGroup -n $storage --query id -o tsv
    & .\advanced\New-PrivateEndpoint.ps1 -ResourceGroup $Lab.ResourceGroup -Location $Lab.Location -Name "$($Lab.Prefix)-backup-pe" -ResourceId $storageId -GroupId blob -SubnetId $out.endpointsSubnetId.value -VnetId $out.vnetId.value -ZoneName 'privatelink.blob.core.windows.net'
    az storage container-rm create -g $Lab.ResourceGroup --storage-account $storage --name aksbackup -o none
    az dataprotection backup-vault create -g $Lab.ResourceGroup --vault-name $vault -l $Lab.Location --type SystemAssigned --storage-settings datastore-type=VaultStore type=LocallyRedundant -o none
    az dataprotection backup-policy get-default-policy-template --datasource-type AzureKubernetesService -o json | Set-Content "$directory\backup-policy.json"
    az dataprotection backup-policy create -g $Lab.ResourceGroup --vault-name $vault -n disk-policy --policy "$directory\backup-policy.json" -o none
    az k8s-extension create --name azure-aks-backup --extension-type microsoft.dataprotection.kubernetes --scope cluster --cluster-type managedClusters --cluster-name $Lab.ClusterName -g $Lab.ResourceGroup --release-train stable --configuration-settings blobContainer=aksbackup "storageAccount=$storage" "storageAccountResourceGroup=$($Lab.ResourceGroup)" "storageAccountSubscriptionId=$($Lab.SubscriptionId)" -o none
    $extensionPrincipal = az k8s-extension show -n azure-aks-backup --cluster-type managedClusters --cluster-name $Lab.ClusterName -g $Lab.ResourceGroup --query aksAssignedIdentity.principalId -o tsv
    az role assignment create --assignee-object-id $extensionPrincipal --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Contributor' --scope $storageId -o none
    az aks trustedaccess rolebinding create --cluster-name $Lab.ClusterName -g $Lab.ResourceGroup -n aks-backup --roles Microsoft.DataProtection/backupVaults/backup-operator --source-resource-id $vaultId -o none
    $config = az dataprotection backup-instance initialize-backupconfig --datasource-type AzureKubernetesService -o json | ConvertFrom-Json
    $config.included_namespaces = @('storage-lab')
    $config.include_cluster_scope_resources = $true
    $config.snapshot_volumes = $true
    $config | ConvertTo-Json -Depth 30 | Set-Content $configPath
    az dataprotection backup-instance initialize --datasource-id $out.clusterId.value --datasource-location $Lab.Location --datasource-type AzureKubernetesService --policy-id "$vaultId/backupPolicies/disk-policy" --backup-configuration $configPath --friendly-name storage-lab --snapshot-resource-group-name $snapshotRg -o json | Set-Content $instancePath
    az dataprotection backup-instance update-msi-permissions --datasource-type AzureKubernetesService --operation Backup --permissions-scope ResourceGroup --vault-name $vault -g $Lab.ResourceGroup --backup-instance $instancePath --yes -o none
    az dataprotection backup-instance validate-for-backup --backup-instance $instancePath --ids $vaultId
    az dataprotection backup-instance create --backup-instance $instancePath -g $Lab.ResourceGroup --vault-name $vault -o json | Set-Content "$directory\backup-created.json"
    Write-Host 'Configuration submitted. Wait for protection and extension health before Backup.'
    return
}
$instance = Get-Content "$directory\backup-created.json" -Raw | ConvertFrom-Json
if ($Operation -eq 'Backup') {
    $policy = az dataprotection backup-policy show -g $Lab.ResourceGroup --vault-name $vault -n disk-policy -o json | ConvertFrom-Json
    $rule = @($policy.properties.policyRules | Where-Object objectType -EQ AzureBackupRule)[0].name
    if (-not $rule) { throw 'Could not identify the backup rule.' }
    az dataprotection backup-instance adhoc-backup --rule-name $rule --ids $instance.id
    Write-Host 'Backup submitted, not yet verified. Inspect job and recovery point before proceeding.'
    return
}
if (-not $RecoveryPointId) { throw 'Restore requires a verified successful RecoveryPointId.' }
$restoreConfig = az dataprotection backup-instance initialize-restoreconfig --datasource-type AzureKubernetesService -o json | ConvertFrom-Json
$restoreConfig.included_namespaces = @('storage-lab')
$restoreConfig.namespace_mappings = @{ 'storage-lab' = 'storage-restored' }
$restoreConfig.conflict_policy = 'Skip'
$restoreConfig.persistent_volume_restore_mode = 'RestoreWithVolumeData'
$restoreConfig | ConvertTo-Json -Depth 30 | Set-Content "$directory\restore-config.json"
az dataprotection backup-instance restore initialize-for-item-recovery --datasource-type AzureKubernetesService --restore-location $Lab.Location --source-datastore OperationalStore --recovery-point-id $RecoveryPointId --restore-configuration "$directory\restore-config.json" --backup-instance-id $instance.id -o json | Set-Content "$directory\restore-request.json"
az dataprotection backup-instance update-msi-permissions --datasource-type AzureKubernetesService --operation Restore --permissions-scope Resource -g $Lab.ResourceGroup --vault-name $vault --restore-request-object "$directory\restore-request.json" --snapshot-resource-group-id "/subscriptions/$($Lab.SubscriptionId)/resourceGroups/$snapshotRg" --yes
az dataprotection backup-instance validate-for-restore --backup-instance-name $instance.name -g $Lab.ResourceGroup --vault-name $vault --restore-request-object "$directory\restore-request.json"
az dataprotection backup-instance restore trigger --backup-instance-name $instance.name -g $Lab.ResourceGroup --vault-name $vault --restore-request-object "$directory\restore-request.json"
Write-Host 'Restore submitted. Require a successful job and matching data hash; submission is not recovery.'
