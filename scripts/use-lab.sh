#!/usr/bin/env bash
# Source this file to load the cumulative lab state into the current shell.
set -euo pipefail
Root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=lib.sh
source "$Root/scripts/lib.sh"
[[ -f $Root/local.settings.json ]] || die 'Create local.settings.json using the README bootstrap instructions first.'
Lab=$(jq -e . "$Root/local.settings.json")
lab_value() { jq -er --arg key "$1" '.[$key]' <<<"$Lab"; }
output_value() { jq -er --arg key "$1" '.[$key].value' <<<"$Outputs"; }
for name in SubscriptionId TenantId AdminGroupObjectId; do
    value=$(lab_value "$name")
    [[ $value =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ &&
       $value != 00000000-0000-0000-0000-000000000000 ]] || die "Set a valid $name in local.settings.json."
done
[[ $(lab_value Prefix) =~ ^[a-z][a-z0-9]{3,11}$ ]] || die 'Prefix must be 4-12 lowercase letters/digits, starting with a letter.'
[[ $(lab_value Namespace) == orders ]] || die 'This cumulative curriculum requires Namespace=orders.'
[[ $(lab_value ImageTag) =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die 'ImageTag must be a valid OCI image tag.'
account=$(az account show -o json)
[[ $(jq -er .id <<<"$account") == "$(lab_value SubscriptionId)" &&
   $(jq -er .tenantId <<<"$account") == "$(lab_value TenantId)" ]] ||
    die "Wrong Azure context. Run az account set --subscription $(lab_value SubscriptionId), then retry."
Lab=$(jq '. + {ClusterName: (.Prefix + "-aks"), WorkspaceName: (.Prefix + "-logs"), GrafanaName: (.Prefix + "-grafana")}' <<<"$Lab")
deployments=$(az deployment group list -g "$(lab_value ResourceGroup)" --query '[].name' -o json)
Outputs='{}'
if jq -e 'index("foundation") != null' <<<"$deployments" >/dev/null; then
    Outputs=$(az deployment group show -g "$(lab_value ResourceGroup)" -n foundation --query properties.outputs -o json)
    Lab=$(jq --argjson outputs "$Outputs" '. + {
        AcrName: $outputs.acrName.value, RegistryServer: $outputs.registryServer.value,
        KeyVaultName: $outputs.keyVaultName.value, ServiceBusName: $outputs.serviceBusName.value
    }' <<<"$Lab")
fi
