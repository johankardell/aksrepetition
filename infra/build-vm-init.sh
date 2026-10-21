#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
kubernetes_version=$(jq -er .kubernetesVersion /etc/aks-lab/build-config.json)
username=$(jq -er .username /etc/aks-lab/build-config.json)
[[ $username =~ ^[a-z][a-z0-9_]{0,30}$ ]] || {
    printf '%s\n' 'Invalid build VM username.' >&2; exit 1;
}
[[ $kubernetes_version =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]] || {
    printf '%s\n' 'Invalid Kubernetes version in build VM configuration.' >&2; exit 1;
}
minor=${kubernetes_version%.*}
work=$(mktemp -d /var/tmp/aks-build-bootstrap.XXXXXX)
trap 'Result=$?; find "$work" -mindepth 1 -maxdepth 1 -type f -delete || Result=$?; rmdir -- "$work" || Result=$?; exit "$Result"' EXIT
mkdir -p /etc/apt/keyrings /var/lib/aks-lab
curl --fail --silent --show-error --location --retry 5 https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor --yes -o /etc/apt/keyrings/microsoft.gpg
curl --fail --silent --show-error --location --retry 5 https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /etc/apt/keyrings/githubcli.gpg
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/azure-cli/ noble main' \
    > /etc/apt/sources.list.d/azure-cli.list
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main' \
    > /etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install -y azure-cli gh
cli_version=$(az version -o json | jq -er '."azure-cli"')
dpkg --compare-versions "$cli_version" ge 2.90.0 || {
    printf 'Azure CLI 2.90+ is required; installed %s.\n' "$cli_version" >&2; exit 1;
}
download() { curl --fail --silent --show-error --location --retry 5 "$1" -o "$work/$2"; }
verify_hash() {
    local hash
    hash=$(awk '{print $1}' "$work/$2")
    [[ $hash =~ ^[a-fA-F0-9]{64}$ ]] || { printf 'Invalid checksum for %s\n' "$1" >&2; exit 1; }
    printf '%s  %s\n' "$hash" "$work/$1" | sha256sum --check
}
kubectl_version=$(curl --fail --silent --show-error --location --retry 5 "https://dl.k8s.io/release/stable-$minor.txt")
[[ $kubectl_version =~ ^v[0-9]+[.][0-9]+[.][0-9]+$ && ${kubectl_version%.*} == "v$minor" ]] || {
    printf 'No valid kubectl release for Kubernetes minor %s.\n' "$minor" >&2; exit 1;
}
download "https://dl.k8s.io/release/$kubectl_version/bin/linux/amd64/kubectl" kubectl
download "https://dl.k8s.io/release/$kubectl_version/bin/linux/amd64/kubectl.sha256" kubectl.sha256
verify_hash kubectl kubectl.sha256
install -m 0755 "$work/kubectl" /usr/local/bin/kubectl
download https://get.helm.sh/helm-v3.22.0-linux-amd64.tar.gz helm.tar.gz
download https://get.helm.sh/helm-v3.22.0-linux-amd64.tar.gz.sha256sum helm.sha256
verify_hash helm.tar.gz helm.sha256
tar -xOf "$work/helm.tar.gz" linux-amd64/helm > "$work/helm"
install -m 0755 "$work/helm" /usr/local/bin/helm
download https://github.com/fluxcd/flux2/releases/download/v2.9.6/flux_2.9.6_linux_amd64.tar.gz flux.tar.gz
download https://github.com/fluxcd/flux2/releases/download/v2.9.6/flux_2.9.6_checksums.txt flux.sha256
awk '$2 == "flux_2.9.6_linux_amd64.tar.gz" {print $1}' "$work/flux.sha256" > "$work/flux-selected.sha256"
verify_hash flux.tar.gz flux-selected.sha256
tar -xOf "$work/flux.tar.gz" flux > "$work/flux"
install -m 0755 "$work/flux" /usr/local/bin/flux
download https://github.com/Azure/kubelogin/releases/download/v0.2.20/kubelogin-linux-amd64.zip kubelogin.zip
download https://github.com/Azure/kubelogin/releases/download/v0.2.20/kubelogin-linux-amd64.zip.sha256 kubelogin.sha256
verify_hash kubelogin.zip kubelogin.sha256
unzip -p "$work/kubelogin.zip" bin/linux_amd64/kubelogin > "$work/kubelogin"
install -m 0755 "$work/kubelogin" /usr/local/bin/kubelogin
sudo -H -u "$username" az bicep install
sudo -H -u "$username" az extension add --name aks-preview --version 22.0.0b5
sudo -H -u "$username" az extension add --name bastion --version 1.4.3
sudo -H -u "$username" az extension add --name k8s-extension
for mapping in subuid subgid; do
    if ! grep -q "^$username:" "/etc/$mapping"; then
        start=$(awk -F: 'BEGIN {end=100000} $2+$3>end {end=$2+$3} END {printf "%d\n", end}' "/etc/$mapping")
        option=--add-subuids
        [[ $mapping != subgid ]] || option=--add-subgids
        usermod "$option" "$start-$((start+65535))" "$username"
    fi
done
loginctl enable-linger "$username"
chmod 0700 "/home/$username"
builder_uid=$(id -u "$username")
systemctl start "user@$builder_uid.service"
sudo -H -u "$username" env "XDG_RUNTIME_DIR=/run/user/$builder_uid" podman info --format json \
    | jq -e '.host.security.rootless == true'
build_help=$(podman build --help)
grep -q -- '--sbom' <<< "$build_help" || {
    printf '%s\n' 'Installed Podman lacks the required SBOM build support.' >&2; exit 1;
}
if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi
{
    az version
    kubectl version --client
    kubelogin --version
    helm version --short
    flux version --client
    podman version
    python3 --version
    psql --version
    gh --version
} > /var/lib/aks-lab/tool-versions.txt
touch /var/lib/aks-lab/build-ready
