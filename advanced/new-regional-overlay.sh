#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

output_directory='' registry_server='' image_digest='' service_bus_namespace=''
api_client_id='' worker_client_id='' postgres_host='' postgres_private_ip=''
api_role='orders_api_dr' worker_role='orders_worker_dr' api_replicas=0 worker_replicas=0
parse_args "$@"
for parameter in output_directory registry_server image_digest service_bus_namespace api_client_id worker_client_id postgres_host postgres_private_ip; do
    [[ -n ${!parameter} ]] || die "--${parameter//_/-} is required."
done
[[ $image_digest =~ ^sha256:[a-f0-9]{64}$ ]] || die '--image-digest must match ^sha256:[a-f0-9]{64}$.'
for parameter in registry_server service_bus_namespace api_client_id worker_client_id postgres_host postgres_private_ip api_role worker_role; do
    value=${!parameter}
    [[ $value != *$'\r'* && $value != *$'\n'* && $value != *"'"* && $value != *'"'* ]] \
        || die 'Use single-line unquoted parameter values.'
done
for parameter in api_replicas worker_replicas; do
    [[ ${!parameter} =~ ^\+?0*([0-9]{1,2})$ ]] || die "--${parameter//_/-} must be an integer from 0 to 10."
    value=$((10#${BASH_REMATCH[1]}))
    (( value <= 10 )) || die "--${parameter//_/-} must be an integer from 0 to 10."
    printf -v "$parameter" '%s' "$value"
done
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 - "$root/k8s/base" "$output_directory" "$registry_server" "$service_bus_namespace" "$api_client_id" "$worker_client_id" <<'PY'
import json
import pathlib
import re
import sys

source, output, registry, service_bus, api_id, worker_id = sys.argv[1:]
base = pathlib.Path(output) / "base"
base.mkdir(parents=True, exist_ok=True)
for path in pathlib.Path(source).glob("*.yaml"):
    text = path.read_text()
    text = text.replace("__REGISTRY__/order-app", json.dumps(registry + "/order-app"))
    text = text.replace("SERVICEBUS_NAMESPACE=__SERVICEBUS_NAMESPACE__",
                        json.dumps("SERVICEBUS_NAMESPACE=" + service_bus))
    text = text.replace("__API_CLIENT_ID__", json.dumps(api_id))
    text = text.replace("__WORKER_CLIENT_ID__", json.dumps(worker_id))
    text = text.replace("__IMAGE_TAG__", "v1")
    if re.search(r"__[A-Z][A-Z0-9_]*__", text):
        sys.exit(f"Unresolved placeholder in {path}.")
    if path.name == "kustomization.yaml":
        text = re.sub(r"(?m)^  - namespace[.]yaml\r?\n", "", text)
    (base / path.name).write_text(text)
PY
registry_image_yaml=$(jq -n --arg value "$registry_server/order-app" '$value')
postgres_host_yaml=$(jq -n --arg value "$postgres_host" '$value')
postgres_cidr_yaml=$(jq -n --arg value "$postgres_private_ip/32" '$value')
api_role_yaml=$(jq -n --arg value "$api_role" '$value')
worker_role_yaml=$(jq -n --arg value "$worker_role" '$value')
cat > "$output_directory/kustomization.yaml" <<YAML
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - base
  - network.yaml
patches:
  - path: database.yaml
replicas:
  - name: order-api
    count: $api_replicas
  - name: order-worker
    count: $worker_replicas
images:
  - name: $registry_image_yaml
    newName: $registry_image_yaml
    digest: $image_digest
YAML
cat > "$output_directory/database.yaml" <<YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-api
  namespace: orders
spec:
  template:
    spec:
      containers:
        - name: api
          env:
            - {name: POSTGRES_HOST, value: $postgres_host_yaml}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: $api_role_yaml}
            - {name: POSTGRES_PORT, value: "5432"}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-worker
  namespace: orders
spec:
  template:
    spec:
      containers:
        - name: worker
          env:
            - {name: POSTGRES_HOST, value: $postgres_host_yaml}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: $worker_role_yaml}
            - {name: POSTGRES_PORT, value: "5432"}
YAML
cat > "$output_directory/network.yaml" <<YAML
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: regional-default-deny
  namespace: orders
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: regional-dependencies
  namespace: orders
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: kube-system}
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
    - to:
        - ipBlock: {cidr: 0.0.0.0/0}
      ports: [{protocol: TCP, port: 443}]
    - to:
        - ipBlock: {cidr: $postgres_cidr_yaml}
      ports: [{protocol: TCP, port: 5432}]
    - to:
        - podSelector:
            matchLabels: {app: order-api}
      ports: [{protocol: TCP, port: 8080}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: origin-ingress
  namespace: orders
spec:
  podSelector:
    matchLabels: {app: regional-origin}
  policyTypes: [Ingress]
  ingress:
    - ports: [{protocol: TCP, port: 8443}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: regional-api
  namespace: orders
spec:
  podSelector:
    matchLabels: {app: order-api}
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels: {app: regional-origin}
      ports: [{protocol: TCP, port: 8080}]
YAML
rendered=$(kubectl kustomize "$output_directory")
if grep -Eq '^kind: (HorizontalPodAutoscaler|ScaledObject|TriggerAuthentication|ClusterTriggerAuthentication)[[:space:]]*$|__SCALER_CLIENT_ID__' <<< "$rendered"; then
    die 'The fixed-replica DR overlay must not inherit autoscalers or scaler authentication. Keep scaling assets out of k8s/base.'
fi
if grep -Eq '__[A-Z][A-Z0-9_]*__' <<< "$rendered"; then
    die 'The regional overlay contains unresolved placeholders.'
fi
printf 'Created cold application overlay at %s. No HPA/KEDA is included.\n' "$output_directory"
