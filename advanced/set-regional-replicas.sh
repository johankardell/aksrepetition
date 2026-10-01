#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "$0")/../scripts" && pwd)/lib.sh"

directory='./advanced/regions/secondary' api='' worker=''
parse_args "$@"
for parameter in api worker; do
    value=$(parameter_value "$parameter")
    [[ -n $value ]] || die "--$parameter is required."
    [[ $value =~ ^[+]?0*([0-9]{1,2})$ ]] || die "--$parameter must be an integer from 0 to 10."
    value=${value#+}
    value=$((10#$value))
    (( value <= 10 )) || die "--$parameter must be an integer from 0 to 10."
    printf -v "$parameter" '%s' "$value"
done
python3 - "$directory/kustomization.yaml" "$api" "$worker" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
for name, count in zip(("order-api", "order-worker"), sys.argv[2:]):
    pattern = re.compile(r"(?m)(  - name: " + re.escape(name) + r"\r?\n    count: )\d+")
    if len(pattern.findall(text)) != 1:
        sys.exit(f"Expected exactly one replica entry for {name}.")
    text = pattern.sub(lambda match: match[1] + count, text)
path.write_text(text)
PY
kubectl kustomize "$directory" >/dev/null
printf '%s\n' 'Replica source updated. Review, commit, push and reconcile before treating it as effective.'
