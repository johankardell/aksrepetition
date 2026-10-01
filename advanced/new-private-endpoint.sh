#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

resource_group='' location='' name='' resource_id='' group_id='' subnet_id='' vnet_id='' zone_name=''
parse_args "$@"
for parameter in resource_group location name resource_id group_id subnet_id vnet_id zone_name; do
    [[ -n ${!parameter} ]] || die "--${parameter//_/-} is required."
done

az network private-dns zone create -g "$resource_group" -n "$zone_name" -o none
zone_id=$(az network private-dns zone show -g "$resource_group" -n "$zone_name" --query id -o tsv)
links=$(az network private-dns link vnet list -g "$resource_group" -z "$zone_name" -o json)
link_exists=$(jq -r --arg id "$vnet_id" 'any(.[]; (.virtualNetwork.id | ascii_downcase) == ($id | ascii_downcase))' <<< "$links")
if [[ $link_exists != true ]]; then
    az network private-dns link vnet create -g "$resource_group" -z "$zone_name" -n "$name-link" \
        --virtual-network "$vnet_id" --registration-enabled false -o none
fi
az network private-endpoint create -g "$resource_group" -n "$name" -l "$location" --subnet "$subnet_id" \
    --private-connection-resource-id "$resource_id" --group-ids "$group_id" --connection-name "$name" -o none
az network private-endpoint dns-zone-group create -g "$resource_group" --endpoint-name "$name" -n default \
    --private-dns-zone "$zone_id" --zone-name "$group_id" -o none
az network private-endpoint show -g "$resource_group" -n "$name" \
    --query '{id:id,state:privateLinkServiceConnections[0].privateLinkServiceConnectionState.status}' -o json
