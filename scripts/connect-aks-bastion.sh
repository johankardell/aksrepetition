#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd -- "$(dirname -- "$0")" && pwd)/lib.sh"

resource_group='' cluster_name='' bastion_id='' context='' port=8443
parse_args "$@"
[[ $port =~ ^[0-9]{1,5}$ ]] || die 'Port must be an integer between 1024 and 65535.'
validate_range Port "$port" 1024 65535
port=$((10#$port))
[[ -z $context || $context =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || die 'Use a simple, unique kubeconfig context name.'
if [[ -z $resource_group && -z $cluster_name && -z $bastion_id ]]; then
    # shellcheck source=use-lab.sh
    source "$(dirname -- "$0")/use-lab.sh"
    resource_group=$(lab_value ResourceGroup)
    cluster_name=$(lab_value ClusterName)
    bastion_id=$(az deployment group show -g "$resource_group" -n bastion --query properties.outputs.bastionId.value -o tsv)
else
    [[ -n $resource_group && -n $cluster_name && -n $bastion_id ]] \
        || die 'For an explicit target, supply --resource-group, --cluster-name and --bastion-id together.'
fi
[[ $(lowercase "$bastion_id") =~ ^/subscriptions/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/resourcegroups/[^/[:space:]]+/providers/microsoft[.]network/bastionhosts/[^/[:space:]]+$ ]] \
    || die 'Supply the exact Bastion ARM resource ID.'
[[ -n $context ]] || context="$cluster_name-bastion"
[[ $context =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || die 'Use a simple, unique kubeconfig context name.'
az aks bastion tunnel --help >/dev/null
az network bastion tunnel --help >/dev/null
cluster=$(az aks show -g "$resource_group" -n "$cluster_name" -o json)
jq -e '.apiServerAccessProfile.enablePrivateCluster == true
    and (.apiServerAccessProfile | if has("enablePrivateClusterPublicFqdn")
        then .enablePrivateClusterPublicFqdn else .enablePrivateClusterPublicFQDN end) == false
    and .disableLocalAccounts == true
    and .aadProfile.managed == true
    and (.aadProfile | if has("enableAzureRbac") then .enableAzureRbac else .enableAzureRBAC end) == true
    and (.privateFqdn | type == "string" and length > 0)' <<<"$cluster" >/dev/null \
    || die 'Bastion access requires the private, Entra-integrated lab cluster with local accounts and public FQDN disabled.'
bastion=$(az network bastion show --ids "$bastion_id" -o json)
jq -e '(.sku.name == "Standard" or .sku.name == "Premium")
    and .enableTunneling == true and .provisioningState == "Succeeded"' <<<"$bastion" >/dev/null \
    || die 'Bastion must be provisioned with Standard/Premium SKU and native client tunneling enabled.'
umask 077
# The reviewed preview CLI does not quote the kubeconfig path in its Bash startup export.
kubeconfig=$(mktemp /tmp/aks-bastion-kubeconfig.XXXXXX)
trap 'Status=$?; rm -f -- "$kubeconfig" || Status=$?; exit "$Status"' EXIT
az aks get-credentials -g "$resource_group" -n "$cluster_name" --file "$kubeconfig"
if [[ $context != "$cluster_name" ]]; then
    kubectl --kubeconfig "$kubeconfig" config rename-context "$cluster_name" "$context" >/dev/null
fi
kubelogin convert-kubeconfig --kubeconfig "$kubeconfig" --login azurecli
printf 'Opening %s via Bastion on localhost:%s using temporary kubeconfig %s.\n' "$context" "$port" "$kubeconfig"
printf '%s\n' 'Run the lab commands inside the new subshell. Exit closes the tunnel and removes this kubeconfig.'
az aks bastion tunnel -g "$resource_group" -n "$cluster_name" \
    --bastion "$bastion_id" --port "$port" --kubeconfig-path "$kubeconfig"
