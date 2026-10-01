#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/../scripts/use-lab.sh"
(($# == 0)) || die 'initialize-gitops.sh takes no arguments.'
destination=$Root/gitops/clusters/primary
[[ ! -f $destination/apps/orders/kustomization.yaml ]] || die 'GitOps is already initialized. Edit its sources; do not recopy rendered manifests.'
source=$Root/rendered/base
for required in kustomization.yaml secret-provider.yaml identity-patch.yaml policies.yaml; do
    [[ -f $source/$required ]] || die "Missing accumulated rendered base file $required. Complete labs 2 and 3; do not rerender."
done
kubectl kustomize "$source" >/dev/null
delivery=$(az deployment group show -g "$(lab_value ResourceGroup)" -n delivery --query properties.outputs -o json)
python3 - "$source" "$destination" "$Outputs" "$delivery" <<'PY'
import json, pathlib, re, shutil, sys
source, destination = map(pathlib.Path, sys.argv[1:3])
outputs, delivery = map(json.loads, sys.argv[3:])
for namespace in ("orders", "orders-test"):
    path = destination / "apps" / namespace
    path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, path, dirs_exist_ok=True)
    for file in path.rglob("*.yaml"):
        text = file.read_text()
        if file.name == "kustomization.yaml":
            text = re.sub(r"(?m)^[ \t]*-[ \t]*namespace\.yaml[ \t]*\r?\n", "", text)
        if namespace == "orders-test":
            if file.name in ("secret-provider.yaml", "identity-patch.yaml"):
                file.unlink()
                continue
            if file.name == "kustomization.yaml":
                text = re.sub(r"(?m)^[ \t]*-[ \t]*(?:path:[ \t]*)?(?:secret-provider|identity-patch)\.yaml[ \t]*\r?\n", "", text)
            text = re.sub(r"namespace: orders\b", "namespace: orders-test", text)
            text = text.replace("QUEUE_NAME=orders", "QUEUE_NAME=orders-test")
            text = text.replace(outputs["apiClientId"]["value"], delivery["testApiClientId"]["value"])
            text = text.replace(outputs["workerClientId"]["value"], delivery["testWorkerClientId"]["value"])
        if re.search(r"__[A-Z_]+__", text):
            raise SystemExit(f"Unresolved token in {file}")
        file.write_text(text)
PY
for namespace in orders orders-test; do
    kubectl kustomize "$destination/apps/$namespace" >/dev/null
done
printf 'Initialized application sources once. Review the diff before committing.\n'
