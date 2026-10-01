#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

host_name='' private_ip=''
parse_args "$@"
[[ $host_name =~ ^[a-z0-9.-]+$ ]] || die '--host-name is required and must match ^[a-z0-9.-]+$.'
[[ $private_ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] \
    || die '--private-ip is required and must be a dotted IPv4 value.'
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
directory='./.artifacts/advanced'
mkdir -p -- "$directory"
cat > "$directory/database-patch.yaml" <<YAML
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
            - {name: POSTGRES_HOST, value: "$host_name"}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: orders_api}
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
            - {name: POSTGRES_HOST, value: "$host_name"}
            - {name: POSTGRES_DATABASE, value: ordersdb}
            - {name: POSTGRES_USER, value: orders_worker}
            - {name: POSTGRES_PORT, value: "5432"}
YAML
cat > "$directory/database-egress.yaml" <<YAML
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: database-egress
  namespace: orders
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock: {cidr: $private_ip/32}
      ports: [{protocol: TCP, port: 5432}]
YAML
bash "$root/ops/add-gitops-file.sh" --source "$directory/database-patch.yaml" --kind Patch --namespace orders
bash "$root/ops/add-gitops-file.sh" --source "$directory/database-egress.yaml" --kind Resource --namespace orders
printf '%s\n' 'Updated primary Git source only. Review, commit, push and reconcile orders.'
