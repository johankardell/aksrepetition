#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "$0")/../scripts" && pwd)/lib.sh"

origin_host='' output_directory='' image=''
parse_args "$@"
[[ $origin_host =~ ^[a-z0-9.-]+$ ]] || die '--origin-host is required and must match ^[a-z0-9.-]+$.'
[[ -n $output_directory ]] || die '--output-directory is required.'
[[ $image =~ @sha256:[a-f0-9]{64}$ ]] || die '--image must end with @sha256: followed by 64 lowercase hex digits.'
[[ $image != *$'\r'* && $image != *$'\n'* ]] || die 'Image must be a single value.'
image_yaml=$(jq -n --arg image "$image" '$image')
mkdir -p -- "$output_directory"
cat > "$output_directory/origin.yaml" <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: regional-origin
  namespace: orders
data:
  default.conf: |
    server {
      listen 8443 ssl;
      server_name $origin_host;
      ssl_certificate /tls/tls.crt;
      ssl_certificate_key /tls/tls.key;
      ssl_protocols TLSv1.2 TLSv1.3;
      location / {
        proxy_pass http://order-api.orders.svc.cluster.local;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
      }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: regional-origin
  namespace: orders
spec:
  replicas: 2
  selector:
    matchLabels: {app: regional-origin}
  template:
    metadata:
      labels: {app: regional-origin}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 101
        fsGroup: 101
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: proxy
          image: $image_yaml
          ports: [{containerPort: 8443}]
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: [ALL]}
          resources:
            requests: {cpu: 100m, memory: 64Mi}
            limits: {cpu: 500m, memory: 128Mi}
          readinessProbe:
            httpGet: {path: /readyz, port: 8443, scheme: HTTPS}
            periodSeconds: 5
          volumeMounts:
            - {name: config, mountPath: /etc/nginx/conf.d, readOnly: true}
            - {name: tls, mountPath: /tls, readOnly: true}
      volumes:
        - name: config
          configMap: {name: regional-origin}
        - name: tls
          secret: {secretName: regional-origin-tls}
---
apiVersion: v1
kind: Service
metadata:
  name: regional-origin
  namespace: orders
  annotations:
    service.beta.kubernetes.io/azure-load-balancer-internal: "true"
    service.beta.kubernetes.io/azure-pls-create: "true"
    service.beta.kubernetes.io/azure-pls-name: "orders-origin"
    service.beta.kubernetes.io/azure-pls-visibility: "*"
spec:
  type: LoadBalancer
  externalTrafficPolicy: Cluster
  ports: [{name: https, port: 443, targetPort: 8443}]
  selector: {app: regional-origin}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: regional-origin
  namespace: orders
spec:
  minAvailable: 1
  selector:
    matchLabels: {app: regional-origin}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: origin-private-ingress
  namespace: orders
spec:
  podSelector:
    matchLabels: {app: regional-origin}
  policyTypes: [Ingress, Egress]
  ingress:
    - ports: [{protocol: TCP, port: 8443}]
  egress:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: kube-system}
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
    - to:
        - podSelector:
            matchLabels: {app: order-api}
      ports: [{protocol: TCP, port: 8080}]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: origin-to-api
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
