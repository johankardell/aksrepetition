#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../scripts/lib.sh"
source='' kind=Resource namespace=orders
parse_args "$@"
require_value source "$source"
validate_namespace "$namespace"
[[ $kind == Resource || $kind == Patch ]] || die '--kind must be Resource or Patch.'
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/gitops/clusters/primary/apps/$namespace
name=$(basename -- "$source")
[[ $name =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*\.yaml$ ]] || die 'Supply a YAML filename without directory traversal.'
cp -- "$source" "$root/$name"
python3 - "$root/kustomization.yaml" "$name" "$kind" <<'PY'
import pathlib, re, sys
path, name, kind = pathlib.Path(sys.argv[1]), *sys.argv[2:]
text = path.read_text()
if not re.search(r"(?m)^\s*-\s*(?:path:\s*)?" + re.escape(name) + r"\s*$", text):
    if kind == "Resource":
        if not re.search(r"(?m)^resources:", text):
            raise SystemExit("Missing resources list.")
        text = re.sub(r"(?m)^resources:", f"resources:\n  - {name}", text)
    elif re.search(r"(?m)^patches:", text):
        text = re.sub(r"(?m)^patches:", f"patches:\n  - path: {name}", text)
    else:
        text += f"\npatches:\n  - path: {name}\n"
    path.write_text(text)
PY
kubectl kustomize "$root" >/dev/null
