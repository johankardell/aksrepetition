#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../scripts/lib.sh"
registry_server=
parse_args "$@"
[[ $registry_server =~ ^[a-zA-Z0-9.-]+$ ]] || die 'Supply a DNS hostname for --registry-server.'
manifest=$(flux install --export)
mapfile -t images < <(grep -oE 'ghcr\.io/fluxcd/[^[:space:]]+' <<<"$manifest" | sort -u)
((${#images[@]} >= 4)) || die 'Expected the four standard Flux controller images.'
for image in "${images[@]}"; do
    target=${image/ghcr.io/$registry_server}
    podman pull "$image"
    podman tag "$image" "$target"
    podman push "$target"
done
