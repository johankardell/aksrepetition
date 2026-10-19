#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
ssh_key=''
parse_args "$@"
source "$(dirname -- "$0")/use-lab.sh"
[[ -n $ssh_key ]] || ssh_key="$Root/rendered/keys/build-vm"
[[ -f $ssh_key ]] || die 'Supply the private build-VM SSH key on this workstation with --ssh-key.'
target=$(az deployment group show -g "$(lab_value ResourceGroup)" -n build-vm --query properties.outputs -o json)
vm_id=$(jq -er '.vmId.value | select(type == "string" and length > 0)' <<< "$target")
username=$(jq -er '.username.value | select(type == "string" and length > 0)' <<< "$target")
bastion_id=$(az deployment group show -g "$(lab_value ResourceGroup)" -n bastion --query properties.outputs.bastionId.value -o tsv)
az network bastion ssh --ids "$bastion_id" --target-resource-id "$vm_id" \
    --auth-type ssh-key --username "$username" --ssh-key "$ssh_key"
