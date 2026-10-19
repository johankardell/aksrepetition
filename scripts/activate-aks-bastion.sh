#!/usr/bin/env bash
# Source this file to expose an AKS Bastion tunnel in the current Bash or Zsh session.

if [ -z "${BASH_VERSION:-}" ] && [ -z "${ZSH_VERSION:-}" ]; then
    printf '%s\n' 'activate-aks-bastion.sh requires Bash or Zsh.' >&2
    return 1 2>/dev/null || exit 1
fi
if [[ -n ${BASH_VERSION:-} && ${BASH_SOURCE[0]} == "$0" ]] ||
   [[ -n ${ZSH_VERSION:-} && ${ZSH_EVAL_CONTEXT:-} != *:file* ]]; then
    printf '%s\n' 'Source this helper: source ./scripts/activate-aks-bastion.sh' >&2
    exit 1
fi

_activate_aks_bastion_error() {
    printf '%s\n' "$*" >&2
    return 1
}

_activate_aks_bastion_remove_file() {
    case $1 in
        /tmp/aks-bastion-kubeconfig.*|/tmp/aks-bastion-tunnel.*.log)
            rm -f -- "$1"
            ;;
    esac
}

disconnect_aks_bastion() {
    local pid=${AKS_BASTION_PID:-} result=0
    if [[ -z $pid && -z ${AKS_BASTION_KUBECONFIG:-} ]]; then
        printf '%s\n' 'No AKS Bastion tunnel is active.' >&2
        return 1
    fi
    if [[ $pid =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || result=$?
        if ((result == 0)); then
            wait "$pid" 2>/dev/null || :
        fi
    fi
    [[ -z ${AKS_BASTION_KUBECONFIG:-} ]] || _activate_aks_bastion_remove_file "$AKS_BASTION_KUBECONFIG"
    [[ -z ${AKS_BASTION_LOG:-} ]] || _activate_aks_bastion_remove_file "$AKS_BASTION_LOG"
    if [[ ${AKS_BASTION_PREVIOUS_KUBECONFIG_SET:-false} == true ]]; then
        export KUBECONFIG=${AKS_BASTION_PREVIOUS_KUBECONFIG:-}
    else
        unset KUBECONFIG
    fi
    unset AKS_BASTION_PID AKS_BASTION_KUBECONFIG AKS_BASTION_LOG
    unset AKS_BASTION_CONTEXT AKS_BASTION_PREVIOUS_KUBECONFIG AKS_BASTION_PREVIOUS_KUBECONFIG_SET
    printf '%s\n' 'AKS Bastion tunnel disconnected.'
    return "$result"
}

_activate_aks_bastion_main() {
    local script_path root resource_group='' cluster_name='' bastion_id='' context='' port=8443
    local option cluster bastion kubeconfig log private_fqdn cluster_id bastion_subscription
    local bastion_resource_group bastion_name pid ready=false attempt previous_kubeconfig='' previous_set=false
    if [[ -n ${ZSH_VERSION:-} ]]; then
        emulate -L zsh
        script_path=${(%):-%x}
    else
        script_path=${BASH_SOURCE[0]}
    fi
    root=$(cd -- "$(dirname -- "$script_path")/.." && pwd) || return

    while (($#)); do
        option=$1
        case $option in
            --resource-group|--cluster-name|--bastion-id|--context|--port)
                (($# >= 2)) || {
                    _activate_aks_bastion_error "Missing value for $option"
                    return
                }
                [[ $2 != --* ]] || {
                    _activate_aks_bastion_error "Missing value for $option"
                    return
                }
                case $option in
                    --resource-group) resource_group=$2 ;;
                    --cluster-name) cluster_name=$2 ;;
                    --bastion-id) bastion_id=$2 ;;
                    --context) context=$2 ;;
                    --port) port=$2 ;;
                esac
                shift 2
                ;;
            *)
                _activate_aks_bastion_error "Unknown option: $option"
                return
                ;;
        esac
    done

    if [[ -n ${AKS_BASTION_PID:-} ]] && [[ $AKS_BASTION_PID =~ ^[0-9]+$ ]] &&
       kill -0 "$AKS_BASTION_PID" 2>/dev/null; then
        _activate_aks_bastion_error "An AKS Bastion tunnel is already active with PID $AKS_BASTION_PID. Run disconnect_aks_bastion first."
        return
    fi
    if [[ -z $resource_group && -z $cluster_name && -z $bastion_id ]]; then
        # shellcheck source=use-lab.sh
        source "$root/scripts/use-lab.sh" || return
        resource_group=$(lab_value ResourceGroup) || return
        cluster_name=$(lab_value ClusterName) || return
        bastion_id=$(az deployment group show -g "$resource_group" -n bastion \
            --query properties.outputs.bastionId.value -o tsv) || return
    else
        [[ -n $resource_group && -n $cluster_name && -n $bastion_id ]] || {
            _activate_aks_bastion_error 'For an explicit target, supply --resource-group, --cluster-name and --bastion-id together.'
            return
        }
    fi

    [[ $port =~ ^[0-9]{1,5}$ ]] && ((10#$port >= 1024 && 10#$port <= 65535)) || {
        _activate_aks_bastion_error 'Port must be an integer between 1024 and 65535.'
        return
    }
    port=$((10#$port))
    [[ -z $context ]] && context="$cluster_name-bastion"
    [[ $context =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || {
        _activate_aks_bastion_error 'Use a simple, unique kubeconfig context name.'
        return
    }
    [[ $(printf '%s' "$bastion_id" | tr '[:upper:]' '[:lower:]') =~ ^/subscriptions/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/resourcegroups/[^/[:space:]]+/providers/microsoft[.]network/bastionhosts/[^/[:space:]]+$ ]] || {
        _activate_aks_bastion_error 'Supply the exact Bastion ARM resource ID.'
        return
    }
    python3 - "$port" <<'PY' || {
import socket
import sys

with socket.socket() as listener:
    listener.bind(("127.0.0.1", int(sys.argv[1])))
PY
        _activate_aks_bastion_error "Local port $port is already in use. Supply another with --port."
        return
    }

    az network bastion tunnel --help >/dev/null || return
    cluster=$(az aks show -g "$resource_group" -n "$cluster_name" -o json) || return
    jq -e '.apiServerAccessProfile.enablePrivateCluster == true
        and (.apiServerAccessProfile | if has("enablePrivateClusterPublicFqdn")
            then .enablePrivateClusterPublicFqdn else .enablePrivateClusterPublicFQDN end) == false
        and .disableLocalAccounts == true
        and .aadProfile.managed == true
        and (.aadProfile | if has("enableAzureRbac") then .enableAzureRbac else .enableAzureRBAC end) == true
        and (.privateFqdn | type == "string" and length > 0)
        and (.id | type == "string" and length > 0)' <<<"$cluster" >/dev/null || {
        _activate_aks_bastion_error 'Bastion access requires the private, Entra-integrated lab cluster with local accounts and public FQDN disabled.'
        return
    }
    bastion=$(az network bastion show --ids "$bastion_id" -o json) || return
    jq -e '(.sku.name == "Standard" or .sku.name == "Premium")
        and .enableTunneling == true and .provisioningState == "Succeeded"
        and (.id | type == "string" and length > 0)' <<<"$bastion" >/dev/null || {
        _activate_aks_bastion_error 'Bastion must be provisioned with Standard/Premium SKU and native client tunneling enabled.'
        return
    }

    private_fqdn=$(jq -er .privateFqdn <<<"$cluster") || return
    cluster_id=$(jq -er .id <<<"$cluster") || return
    bastion_subscription=$(jq -er '.id | split("/")[2]' <<<"$bastion") || return
    bastion_resource_group=$(jq -er '.id | split("/")[4]' <<<"$bastion") || return
    bastion_name=$(jq -er '.id | split("/")[8]' <<<"$bastion") || return
    [[ -n ${KUBECONFIG+x} ]] && {
        previous_set=true
        previous_kubeconfig=$KUBECONFIG
    }

    umask 077
    kubeconfig=$(mktemp /tmp/aks-bastion-kubeconfig.XXXXXX) || return
    log=$(mktemp /tmp/aks-bastion-tunnel.XXXXXX.log) || {
        _activate_aks_bastion_remove_file "$kubeconfig"
        return
    }
    if ! az aks get-credentials -g "$resource_group" -n "$cluster_name" --file "$kubeconfig"; then
        _activate_aks_bastion_remove_file "$kubeconfig"
        _activate_aks_bastion_remove_file "$log"
        return 1
    fi
    if [[ $context != "$cluster_name" ]] &&
       ! kubectl --kubeconfig "$kubeconfig" config rename-context "$cluster_name" "$context" >/dev/null; then
        _activate_aks_bastion_remove_file "$kubeconfig"
        _activate_aks_bastion_remove_file "$log"
        return 1
    fi
    if ! kubectl --kubeconfig "$kubeconfig" config set-cluster "$cluster_name" \
        --server="https://localhost:$port/" --tls-server-name="$private_fqdn" >/dev/null ||
       ! kubelogin convert-kubeconfig --kubeconfig "$kubeconfig" --login azurecli; then
        _activate_aks_bastion_remove_file "$kubeconfig"
        _activate_aks_bastion_remove_file "$log"
        return 1
    fi

    az network bastion tunnel --resource-group "$bastion_resource_group" --name "$bastion_name" \
        --subscription "$bastion_subscription" --target-resource-id "$cluster_id" \
        --resource-port 443 --port "$port" >"$log" 2>&1 &
    pid=$!
    for attempt in {1..30}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            printf 'Bastion tunnel exited before becoming ready. Log: %s\n' "$log" >&2
            tail -n 20 "$log" >&2
            _activate_aks_bastion_remove_file "$kubeconfig"
            _activate_aks_bastion_remove_file "$log"
            return 1
        fi
        if python3 - "$port" <<'PY'
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.25):
    pass
PY
        then
            ready=true
            break
        fi
        sleep 1
    done
    if [[ $ready != true ]]; then
        kill "$pid" 2>/dev/null || :
        wait "$pid" 2>/dev/null || :
        printf 'Bastion tunnel did not listen on localhost:%s. Log: %s\n' "$port" "$log" >&2
        tail -n 20 "$log" >&2
        _activate_aks_bastion_remove_file "$kubeconfig"
        _activate_aks_bastion_remove_file "$log"
        return 1
    fi

    typeset -g AKS_BASTION_PID=$pid
    typeset -g AKS_BASTION_KUBECONFIG=$kubeconfig
    typeset -g AKS_BASTION_LOG=$log
    typeset -g AKS_BASTION_CONTEXT=$context
    typeset -g AKS_BASTION_PREVIOUS_KUBECONFIG=$previous_kubeconfig
    typeset -g AKS_BASTION_PREVIOUS_KUBECONFIG_SET=$previous_set
    export KUBECONFIG=$kubeconfig
    printf 'AKS Bastion tunnel is ready on localhost:%s using context %s.\n' "$port" "$context"
    printf 'KUBECONFIG=%s\n' "$KUBECONFIG"
    printf '%s\n' 'Run disconnect_aks_bastion when finished.'
}

if _activate_aks_bastion_main "$@"; then
    _activate_aks_bastion_status=0
else
    _activate_aks_bastion_status=$?
fi
unset -f _activate_aks_bastion_main _activate_aks_bastion_error
if ((_activate_aks_bastion_status != 0)); then
    unset _activate_aks_bastion_status
    return 1
fi
unset _activate_aks_bastion_status
