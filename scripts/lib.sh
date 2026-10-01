#!/usr/bin/env bash

die() { printf '%s\n' "$*" >&2; exit 1; }

parse_args() {
    local __option __name __value
    while (($#)); do
        __option=$1
        [[ $__option =~ ^--[a-z][a-z0-9-]*$ ]] || die "Expected --option, got: $__option"
        __name=${__option#--}
        __name=${__name//-/_}
        [[ -v $__name ]] || die "Unknown option: $__option"
        case $__option in
            --apply|--confirm) __value=true; shift ;;
            *)
                (($# >= 2)) || die "Missing value for $__option"
                [[ $2 != --* ]] || die "Missing value for $__option"
                __value=$2
                shift 2
                ;;
        esac
        declare -g "$__name=$__value"
    done
}

require_value() { [[ -n $2 ]] || die "Missing --$1"; }
validate_namespace() { [[ $1 == orders || $1 == orders-test ]] || die 'Namespace must be orders or orders-test.'; }
validate_digest() { [[ $1 =~ ^sha256:[a-f0-9]{64}$ ]] || die 'Supply an immutable sha256 image digest.'; }
validate_range() {
    if [[ ! $2 =~ ^[0-9]+$ ]] || ((10#$2 < $3 || 10#$2 > $4)); then
        die "$1 must be an integer between $3 and $4."
    fi
}
