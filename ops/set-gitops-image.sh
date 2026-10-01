#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/../scripts/lib.sh"
namespace='' digest=''
parse_args "$@"
validate_namespace "$namespace"
validate_digest "$digest"
manifest_path=$(cd -- "$(dirname -- "$0")/.." && pwd)/gitops/clusters/primary/apps/$namespace/kustomization.yaml
python3 - "$manifest_path" "$digest" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
pattern = r"(?m)^([ \t]+)(?:newTag|digest):[^\r\n]*"
if len(re.findall(pattern, text)) != 1:
    raise SystemExit("Expected exactly one image transform; edit ambiguous transforms explicitly.")
path.write_text(re.sub(pattern, lambda m: f"{m[1]}digest: {sys.argv[2]}", text))
PY
kubectl kustomize "$(dirname -- "$manifest_path")" >/dev/null
