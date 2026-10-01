#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
registry_name='' registry_server=''
parse_args "$@"
[[ $registry_name =~ ^[a-zA-Z0-9]{5,50}$ ]] || die 'Supply a valid --registry-name.'
[[ $registry_server =~ ^[a-zA-Z0-9.-]+$ ]] || die 'Supply a DNS hostname for --registry-server.'
login=$(az acr login --name "$registry_name" --expose-token -o json)
[[ $(lowercase "$registry_server") == "$(jq -er '.loginServer | ascii_downcase' <<<"$login")" ]] ||
    die 'The returned ACR login server does not match --registry-server.'
token=$(jq -er '.accessToken | select(type == "string" and length > 0)' <<<"$login")
printf '%s' "$token" | podman login "$registry_server" \
    --username 00000000-0000-0000-0000-000000000000 --password-stdin
unset login token
