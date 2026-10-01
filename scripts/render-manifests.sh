#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/use-lab.sh"
(($# == 0)) || die 'render-manifests.sh takes no arguments.'
destination=$Root/rendered/base
mkdir -p "$destination"
replacements=$(jq -n --arg registry "$(lab_value RegistryServer)" --arg tag "$(lab_value ImageTag)" \
    --arg bus "$(lab_value ServiceBusName).servicebus.windows.net" \
    --arg api "$(output_value apiClientId)" --arg worker "$(output_value workerClientId)" \
    '{"__REGISTRY__":$registry,"__IMAGE_TAG__":$tag,"__SERVICEBUS_NAMESPACE__":$bus,"__API_CLIENT_ID__":$api,"__WORKER_CLIENT_ID__":$worker}')
python3 - "$Root/k8s/base" "$destination" "$replacements" <<'PY'
import json, pathlib, re, sys
source, destination = map(pathlib.Path, sys.argv[1:3])
for file in source.glob("*.yaml"):
    text = file.read_text()
    for key, value in json.loads(sys.argv[3]).items():
        text = text.replace(key, value)
    if re.search(r"__[A-Z_]+__", text):
        raise SystemExit(f"Unresolved token in {file.name}")
    (destination / file.name).write_text(text)
PY
kubectl kustomize "$destination" >/dev/null
printf 'Rendered base to %s. Apply only before Flux takes ownership in lab 4.\n' "$destination"
