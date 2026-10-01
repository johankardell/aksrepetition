#!/usr/bin/env bash
# Source this file to load the cumulative lab state into the current shell.

if [ -z "${BASH_VERSION:-}" ] && [ -z "${ZSH_VERSION:-}" ]; then
    printf '%s\n' 'use-lab.sh requires Bash or Zsh.' >&2
    return 1 2>/dev/null || exit 1
fi

_use_lab_error() {
    printf '%s\n' "$*" >&2
    return 1
}

_use_lab_load() {
    local script_path root lab outputs name value account subscription_id tenant_id resource_group deployments deployment deployment_state
    if [[ -n ${ZSH_VERSION:-} ]]; then
        # Keep Zsh's caller-specific options out of the loader without changing them.
        emulate -L zsh
        script_path=${(%):-%x}
    else
        script_path=${BASH_SOURCE[0]}
    fi
    root=$(cd -- "$(dirname -- "$script_path")/.." && pwd) || return
    # shellcheck source=lib.sh
    source "$root/scripts/lib.sh" || return
    [[ -f $root/local.settings.json ]] ||
        _use_lab_error 'Create local.settings.json using the README bootstrap instructions first.' || return
    lab=$(jq -e . "$root/local.settings.json") || return

    for name in SubscriptionId TenantId AdminGroupObjectId; do
        value=$(jq -er --arg key "$name" '.[$key]' <<<"$lab") ||
            _use_lab_error "Set a valid $name in local.settings.json." || return
        [[ $value =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ &&
           $value != 00000000-0000-0000-0000-000000000000 ]] ||
            _use_lab_error "Set a valid $name in local.settings.json." || return
    done
    value=$(jq -er .Prefix <<<"$lab") || return
    [[ $value =~ ^[a-z][a-z0-9]{3,11}$ ]] ||
        _use_lab_error 'Prefix must be 4-12 lowercase letters/digits, starting with a letter.' || return
    value=$(jq -er .Namespace <<<"$lab") || return
    [[ $value == orders ]] ||
        _use_lab_error 'This cumulative curriculum requires Namespace=orders.' || return
    value=$(jq -er .ImageTag <<<"$lab") || return
    [[ $value =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] ||
        _use_lab_error 'ImageTag must be a valid OCI image tag.' || return

    subscription_id=$(jq -er .SubscriptionId <<<"$lab") || return
    tenant_id=$(jq -er .TenantId <<<"$lab") || return
    account=$(az account show -o json) || return
    if [[ $(jq -er .id <<<"$account") != "$subscription_id" ||
          $(jq -er .tenantId <<<"$account") != "$tenant_id" ]]; then
        _use_lab_error "Wrong Azure context. Run az account set --subscription $subscription_id, then retry." || return
    fi

    lab=$(jq '. + {ClusterName: (.Prefix + "-aks"), WorkspaceName: (.Prefix + "-logs"), GrafanaName: (.Prefix + "-grafana")}' <<<"$lab") ||
        return
    resource_group=$(jq -er .ResourceGroup <<<"$lab") || return
    deployments=$(az deployment group list -g "$resource_group" --query '[].name' -o json) || return
    outputs='{}'
    if jq -e 'index("foundation") != null' <<<"$deployments" >/dev/null; then
        deployment=$(az deployment group show -g "$resource_group" -n foundation \
            --query '{provisioningState:properties.provisioningState,outputs:properties.outputs}' -o json) ||
            return
        deployment=$(jq -ce 'select(type == "object"
            and (.provisioningState | type == "string" and length > 0)
            and ((.outputs | type) == "object" or .outputs == null))' <<<"$deployment") ||
            _use_lab_error 'Foundation deployment metadata is empty or invalid; inspect the Azure deployment response.' || return
        deployment_state=$(jq -er '.provisioningState' <<<"$deployment") || return
        outputs=$(jq -c '.outputs // {}' <<<"$deployment") || return
        if [[ $outputs == '{}' ]]; then
            case $deployment_state in
                Failed|Canceled)
                    printf 'Foundation deployment is %s and has no outputs. Loaded bootstrap settings only; inspect the failure before retrying deployment.\n' \
                        "$deployment_state" >&2
                    ;;
                Succeeded)
                    _use_lab_error 'Foundation deployment succeeded but has no outputs; verify it used infra/main.bicep.' || return
                    ;;
                *)
                    _use_lab_error "Foundation deployment is $deployment_state and has no outputs; wait for it to finish before continuing." || return
                    ;;
            esac
        else
            jq -e '. as $outputs | all(["acrName", "registryServer", "keyVaultName", "serviceBusName"][];
                . as $key | $outputs[$key].value | type == "string" and length > 0)' <<<"$outputs" >/dev/null ||
                _use_lab_error 'Foundation outputs are incomplete or invalid; inspect the deployment before continuing.' || return
            lab=$(jq --argjson outputs "$outputs" '. + {
                AcrName: $outputs.acrName.value, RegistryServer: $outputs.registryServer.value,
                KeyVaultName: $outputs.keyVaultName.value, ServiceBusName: $outputs.serviceBusName.value
            }' <<<"$lab") || return
        fi
    fi

    Root=$root
    Lab=$lab
    Outputs=$outputs
    lab_value() { jq -er --arg key "$1" '.[$key]' <<<"$Lab"; }
    output_value() { jq -er --arg key "$1" '.[$key].value' <<<"$Outputs"; }
}

if _use_lab_load; then
    _use_lab_status=0
else
    _use_lab_status=$?
fi
unset -f _use_lab_load _use_lab_error
if ((_use_lab_status != 0)); then
    unset _use_lab_status
    return 1 2>/dev/null || exit 1
fi
unset _use_lab_status
