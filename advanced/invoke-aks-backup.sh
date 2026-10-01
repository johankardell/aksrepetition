#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

operation='' recovery_point_id=''
parse_args "$@"
case ${operation,,} in
    configure|backup|restore) operation=${operation,,} ;;
    *) die '--operation is required and must be Configure, Backup, or Restore.' ;;
esac
if [[ $operation == restore && -z $recovery_point_id ]]; then
    die 'Restore requires a verified successful --recovery-point-id.'
fi
# shellcheck source=../scripts/use-lab.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/use-lab.sh"
resource_group=$(lab_value ResourceGroup)
location=$(lab_value Location)
prefix=$(lab_value Prefix)
subscription_id=$(lab_value SubscriptionId)
cluster_name=$(lab_value ClusterName)
directory='./.artifacts/advanced'
mkdir -p -- "$directory"
vault="$prefix-backup"
snapshot_rg="$resource_group-snapshots"
storage_prefix=${prefix:0:10}
subscription_hex=${subscription_id//-/}
storage="${storage_prefix}backup${subscription_hex:0:8}"
storage=${storage,,}
vault_id="/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.DataProtection/backupVaults/$vault"
config_path="$directory/backup-config.json"
instance_path="$directory/backup-instance.json"

if [[ $operation == configure ]]; then
    endpoints_subnet_id=$(output_value endpointsSubnetId)
    vnet_id=$(output_value vnetId)
    cluster_id=$(output_value clusterId)
    for provider in Microsoft.DataProtection Microsoft.KubernetesConfiguration; do
        az provider register --namespace "$provider" --wait
    done
    az group create -n "$snapshot_rg" -l "$location" -o none
    az storage account create -g "$resource_group" -n "$storage" -l "$location" --kind StorageV2 \
        --sku Standard_LRS --min-tls-version TLS1_2 --public-network-access Disabled --allow-blob-public-access false -o none
    storage_id=$(az storage account show -g "$resource_group" -n "$storage" --query id -o tsv)
    bash "$Root/advanced/new-private-endpoint.sh" --resource-group "$resource_group" --location "$location" \
        --name "$prefix-backup-pe" --resource-id "$storage_id" --group-id blob \
        --subnet-id "$endpoints_subnet_id" --vnet-id "$vnet_id" --zone-name privatelink.blob.core.windows.net
    az storage container-rm create -g "$resource_group" --storage-account "$storage" --name aksbackup -o none
    az dataprotection backup-vault create -g "$resource_group" --vault-name "$vault" -l "$location" \
        --type SystemAssigned --storage-settings datastore-type=VaultStore type=LocallyRedundant -o none
    az dataprotection backup-policy get-default-policy-template --datasource-type AzureKubernetesService -o json \
        > "$directory/backup-policy.json"
    az dataprotection backup-policy create -g "$resource_group" --vault-name "$vault" -n disk-policy \
        --policy "$directory/backup-policy.json" -o none
    az k8s-extension create --name azure-aks-backup --extension-type microsoft.dataprotection.kubernetes \
        --scope cluster --cluster-type managedClusters --cluster-name "$cluster_name" -g "$resource_group" \
        --release-train stable --configuration-settings blobContainer=aksbackup "storageAccount=$storage" \
        "storageAccountResourceGroup=$resource_group" "storageAccountSubscriptionId=$subscription_id" -o none
    extension_principal=$(az k8s-extension show -n azure-aks-backup --cluster-type managedClusters \
        --cluster-name "$cluster_name" -g "$resource_group" --query aksAssignedIdentity.principalId -o tsv)
    az role assignment create --assignee-object-id "$extension_principal" --assignee-principal-type ServicePrincipal \
        --role 'Storage Blob Data Contributor' --scope "$storage_id" -o none
    az aks trustedaccess rolebinding create --cluster-name "$cluster_name" -g "$resource_group" -n aks-backup \
        --roles Microsoft.DataProtection/backupVaults/backup-operator --source-resource-id "$vault_id" -o none
    az dataprotection backup-instance initialize-backupconfig --datasource-type AzureKubernetesService -o json |
        jq '.included_namespaces = ["storage-lab"] | .include_cluster_scope_resources = true | .snapshot_volumes = true' \
        > "$config_path"
    az dataprotection backup-instance initialize --datasource-id "$cluster_id" --datasource-location "$location" \
        --datasource-type AzureKubernetesService --policy-id "$vault_id/backupPolicies/disk-policy" \
        --backup-configuration "$config_path" --friendly-name storage-lab --snapshot-resource-group-name "$snapshot_rg" \
        -o json > "$instance_path"
    az dataprotection backup-instance update-msi-permissions --datasource-type AzureKubernetesService --operation Backup \
        --permissions-scope ResourceGroup --vault-name "$vault" -g "$resource_group" --backup-instance "$instance_path" --yes -o none
    az dataprotection backup-instance validate-for-backup --backup-instance "$instance_path" --ids "$vault_id"
    az dataprotection backup-instance create --backup-instance "$instance_path" -g "$resource_group" \
        --vault-name "$vault" -o json > "$directory/backup-created.json"
    printf '%s\n' 'Configuration submitted. Wait for protection and extension health before Backup.'
    exit 0
fi
instance_id=$(jq -er '.id | select(type == "string" and length > 0)' "$directory/backup-created.json")
if [[ $operation == backup ]]; then
    policy=$(az dataprotection backup-policy show -g "$resource_group" --vault-name "$vault" -n disk-policy -o json)
    rule=$(jq -er '[.properties.policyRules[] | select((.objectType | ascii_downcase) == "azurebackuprule")][0].name | select(type == "string" and length > 0)' <<< "$policy") \
        || die 'Could not identify the backup rule.'
    az dataprotection backup-instance adhoc-backup --rule-name "$rule" --ids "$instance_id"
    printf '%s\n' 'Backup submitted, not yet verified. Inspect job and recovery point before proceeding.'
    exit 0
fi
instance_name=$(jq -er '.name | select(type == "string" and length > 0)' "$directory/backup-created.json")
az dataprotection backup-instance initialize-restoreconfig --datasource-type AzureKubernetesService -o json |
    jq '.included_namespaces = ["storage-lab"] | .namespace_mappings = {"storage-lab": "storage-restored"} |
        .conflict_policy = "Skip" | .persistent_volume_restore_mode = "RestoreWithVolumeData"' \
    > "$directory/restore-config.json"
az dataprotection backup-instance restore initialize-for-item-recovery --datasource-type AzureKubernetesService \
    --restore-location "$location" --source-datastore OperationalStore --recovery-point-id "$recovery_point_id" \
    --restore-configuration "$directory/restore-config.json" --backup-instance-id "$instance_id" -o json \
    > "$directory/restore-request.json"
az dataprotection backup-instance update-msi-permissions --datasource-type AzureKubernetesService --operation Restore \
    --permissions-scope Resource -g "$resource_group" --vault-name "$vault" \
    --restore-request-object "$directory/restore-request.json" \
    --snapshot-resource-group-id "/subscriptions/$subscription_id/resourceGroups/$snapshot_rg" --yes
az dataprotection backup-instance validate-for-restore --backup-instance-name "$instance_name" \
    -g "$resource_group" --vault-name "$vault" --restore-request-object "$directory/restore-request.json"
az dataprotection backup-instance restore trigger --backup-instance-name "$instance_name" \
    -g "$resource_group" --vault-name "$vault" --restore-request-object "$directory/restore-request.json"
printf '%s\n' 'Restore submitted. Require a successful job and matching data hash; submission is not recovery.'
