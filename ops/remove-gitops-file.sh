#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/../scripts/lib.sh"
name='' namespace=orders
parse_args "$@"
[[ $name =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*[.]yaml$ ]] || die 'Supply a YAML filename without directory traversal.'
validate_namespace "$namespace"
root=$(cd -- "$(dirname -- "$0")/.." && pwd)/gitops/clusters/primary/apps/$namespace
python3 - "$root/kustomization.yaml" "$name" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = re.sub(r"(?m)^  - (?:path: )?" + re.escape(sys.argv[2]) + r"[ \t]*\r?\n", "", path.read_text())
path.write_text(text)
PY
rm -f -- "$root/$name"
kubectl kustomize "$root" >/dev/null
