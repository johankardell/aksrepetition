#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
apply=false confirm=false
parse_args "$@"
source "$(dirname -- "$0")/use-lab.sh"
[[ $(lab_value KubernetesVersion) =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]] || die 'Select an explicit supported regional Kubernetes patch.'
key_path=$Root/rendered/keys/aks.pub
[[ -f $key_path ]] || die 'Create rendered/keys/aks.pub using the README bootstrap instructions first.'
ssh_public_key=$(<"$key_path")
[[ $ssh_public_key == ssh-rsa\ * ]] || die 'Supply an RSA public key, not a private key.'
build_key_path=$Root/rendered/keys/build-vm.pub
[[ -f $build_key_path ]] || die 'Create the separate rendered/keys/build-vm.pub using the README bootstrap instructions first.'
build_ssh_public_key=$(<"$build_key_path")
[[ $build_ssh_public_key == ssh-rsa\ * ]] || die 'Supply an RSA public key for the build VM, not a private key.'
build_vm_size=$(lab_value BuildVmSize)
params=("prefix=$(lab_value Prefix)" "location=$(lab_value Location)"
    "kubernetesVersion=$(lab_value KubernetesVersion)" "vmSize=$(lab_value VmSize)"
    "adminGroupObjectId=$(lab_value AdminGroupObjectId)" "sshPublicKey=$ssh_public_key"
    "buildVmSize=$build_vm_size" "buildVmSshPublicKey=$build_ssh_public_key")
az deployment group what-if -g "$(lab_value ResourceGroup)" -n foundation -f "$Root/infra/main.bicep" -p "${params[@]}"
if [[ $apply == true ]]; then
    [[ $confirm == true ]] || die 'Billable deployment requires both --apply and --confirm.'
    printf 'Type %s to deploy billable resources: ' "$(lab_value ResourceGroup)" >&2
    read -r approval || die 'Deployment confirmation was not provided.'
    [[ $approval == "$(lab_value ResourceGroup)" ]] || die 'Deployment not confirmed.'
    az deployment group create -g "$(lab_value ResourceGroup)" -n foundation -f "$Root/infra/main.bicep" -p "${params[@]}" -o none
fi
