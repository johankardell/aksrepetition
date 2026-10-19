# Lab 27 - Build progressively with Kustomize

**Level:** beginner to advanced. **Type:** optional and self-contained. **Scenario:** a platform team wants one reusable application base, environment-specific behavior without copied manifests, and optional production controls that can be composed across workloads.

**Outcome:** a Kustomize base, development and production overlays, a reusable production-safety component, generated configuration, image and replica transformations, targeted patches, replacements, and a repeatable offline verification workflow.

This lab does not use or modify the cumulative labs 10-15 and 20-23 environment. It renders manifests locally and does not require an AKS cluster or Azure resources.

## Prerequisites and working folder

Use Linux Bash, `kubectl` with built-in Kustomize support, and standard Unix tools. Run the lab from the repository root. Check the client before starting:

```bash
set -euo pipefail
kubectl version --client
kubectl kustomize --help >/dev/null
```

Create a disposable working tree:

```bash
Work=$(mktemp -d "${TMPDIR:-/tmp}/aks-kustomize-lab.XXXXXXXX")
mkdir -p "$Work/base" "$Work/overlays/dev" "$Work/overlays/prod" "$Work/components/production-safety"
printf 'Working folder: %s\n' "$Work"

cat > "$Work/base/deployment.yaml" <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app.kubernetes.io/name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: web
  template:
    metadata:
      labels:
        app.kubernetes.io/name: web
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: web
          image: nginx:latest
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: DEPLOYMENT_TIER
              value: unset
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 250m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: cache
              mountPath: /var/cache/nginx
            - name: run
              mountPath: /var/run
      volumes:
        - name: cache
          emptyDir:
            sizeLimit: 64Mi
        - name: run
          emptyDir:
            sizeLimit: 8Mi
YAML

cat > "$Work/base/service.yaml" <<'YAML'
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  selector:
    app.kubernetes.io/name: web
  ports:
    - name: http
      port: 80
      targetPort: http
YAML
```

Do not apply this training workload to the cumulative lab cluster. The image and NGINX configuration are sufficient for rendering practice, not a production deployment contract.

## Tier 1 - Easy: create and transform a base

**Task:** create `base/kustomization.yaml` without changing either resource file. It must:

- include the Deployment and Service;
- put namespaced resources in `kustomize-practice`;
- prefix resource names with `practice-`;
- add `app.kubernetes.io/part-of: kustomize-practice` without changing selectors;
- generate an `app-settings` ConfigMap with `APP_MODE=base` and `LOG_LEVEL=info`;
- replace `nginx:latest` with `nginx:1.27.5-alpine`.

Render the base to `base.yaml`. Prove that the Deployment image, generated ConfigMap, Service selector, and Deployment selector have the expected values. Explain why Kustomize adds a hash to the ConfigMap name and updates references when a generated ConfigMap is consumed by a workload.

<details>
<summary>Solution</summary>

Create the Kustomization:

```bash
cat > "$Work/base/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: kustomize-practice
namePrefix: practice-
resources:
  - deployment.yaml
  - service.yaml
labels:
  - pairs:
      app.kubernetes.io/part-of: kustomize-practice
    includeSelectors: false
configMapGenerator:
  - name: app-settings
    literals:
      - APP_MODE=base
      - LOG_LEVEL=info
images:
  - name: nginx
    newTag: 1.27.5-alpine
YAML

kubectl kustomize "$Work/base" > "$Work/base.yaml"
grep -nE '^(kind:|  name:|  namespace:|    image:|  APP_MODE:|  LOG_LEVEL:)' "$Work/base.yaml"
```

Expected resources are `practice-web` Deployment and Service plus a generated ConfigMap named like `practice-app-settings-<hash>`. The namespace is written into namespaced resources even though this exercise does not create the Namespace.

Inspect selectors separately:

```bash
kubectl kustomize "$Work/base" |
  awk '/^kind: (Deployment|Service)$/{kind=$2} /matchLabels:|selector:/{print kind ": " $0} /app.kubernetes.io\/name: web/{print "  " $0}'
```

The original `app.kubernetes.io/name: web` selector remains stable because the added label has `includeSelectors: false`.

Generator hashes make configuration changes produce a new object name. When a workload references that generated ConfigMap by its original name, Kustomize's name-reference transformer updates the reference to the hashed name, causing a declarative rollout when the pod template changes. This base does not consume the ConfigMap yet; Tier 2 adds that reference.

</details>

## Tier 2 - Medium: create development and production overlays

**Task:** build two overlays that both reuse `../../base`.

The development overlay must:

- prefix names with `dev-`;
- merge `APP_MODE=development` and `LOG_LEVEL=debug` into the generated ConfigMap;
- inject the complete ConfigMap with `envFrom`;
- keep one replica;
- add the pod-template annotation `training.example.com/environment: development`.

The production overlay must:

- prefix names with `prod-`;
- merge `APP_MODE=production` and `LOG_LEVEL=warning`;
- inject the complete ConfigMap with `envFrom`;
- run three replicas;
- raise the Deployment requests to `cpu: 200m` and `memory: 256Mi`;
- add the pod-template annotation `training.example.com/environment: production`.

Use Kustomize fields and targeted patches rather than copying either base resource. Render both overlays and compare their resource names, ConfigMap data, image, replica count, annotations, and requests.

<details>
<summary>Solution</summary>

Create the development overlay:

```bash
cat > "$Work/overlays/dev/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namePrefix: dev-
resources:
  - ../../base
configMapGenerator:
  - name: app-settings
    behavior: merge
    literals:
      - APP_MODE=development
      - LOG_LEVEL=debug
replicas:
  - name: web
    count: 1
patches:
  - target:
      kind: Deployment
      name: web
    patch: |-
      - op: add
        path: /spec/template/metadata/annotations
        value:
          training.example.com/environment: development
      - op: add
        path: /spec/template/spec/containers/0/envFrom
        value:
          - configMapRef:
              name: app-settings
YAML
```

Create the production overlay:

```bash
cat > "$Work/overlays/prod/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namePrefix: prod-
resources:
  - ../../base
configMapGenerator:
  - name: app-settings
    behavior: merge
    literals:
      - APP_MODE=production
      - LOG_LEVEL=warning
replicas:
  - name: web
    count: 3
patches:
  - target:
      kind: Deployment
      name: web
    patch: |-
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: web
      spec:
        template:
          metadata:
            annotations:
              training.example.com/environment: production
          spec:
            containers:
              - name: web
                envFrom:
                  - configMapRef:
                      name: app-settings
                resources:
                  requests:
                    cpu: 200m
                    memory: 256Mi
YAML
```

Render and compare:

```bash
for environment in dev prod; do
  kubectl kustomize "$Work/overlays/$environment" > "$Work/$environment.yaml"
  printf '\n--- %s ---\n' "$environment"
  grep -nE '^(kind:|  name:|  replicas:|  APP_MODE:|  LOG_LEVEL:|      training.example.com/environment:|            cpu:|            memory:|                name: .*app-settings)' \
    "$Work/$environment.yaml"
done
```

The final names begin with the overlay prefix followed by the base prefix, for example `prod-practice-web`. Patch targets use the resource's original name, `web`; Kustomize resolves patches before applying the final name transformations. The `configMapRef` is rewritten to the final prefixed and hashed ConfigMap name.

The development patch is JSON 6902, which is precise for adding fields by path. The production patch is a strategic merge patch, which can merge the named `web` container without restating the full Deployment.

</details>

## Tier 3 - Hard: compose controls, wire values, and debug transformations

**Task:** create a reusable `production-safety` Kustomize component and include it only in the production overlay.

The component must add:

- a PodDisruptionBudget selecting the existing web pods with `maxUnavailable: 1`;
- a default-deny ingress NetworkPolicy;
- a second NetworkPolicy allowing ingress to TCP 8080 only from pods labeled `access: web-client` in the same namespace;
- `pod-security.kubernetes.io/enforce: restricted` as a common label on component resources, without changing selectors.

Then add a Kustomize replacement to the production overlay. Copy `data.APP_MODE` from the generated `app-settings` ConfigMap into the existing `DEPLOYMENT_TIER` environment variable in the `web` container. Do not hard-code `production` in the Deployment patch.

Finally, introduce and diagnose this broken patch target:

```yaml
- target:
    kind: Deployment
    name: prod-practice-web
  patch: |-
    - op: add
      path: /metadata/annotations/training.example.com~1owner
      value: platform-team
```

Explain why it fails, correct it, and verify all of these invariants from the rendered YAML:

- exactly one Deployment, Service, ConfigMap, PodDisruptionBudget, and two NetworkPolicies exist;
- the Deployment has three replicas;
- `DEPLOYMENT_TIER` is `production`;
- the Deployment's ConfigMap reference matches the generated ConfigMap's final name;
- the PodDisruptionBudget and Service selectors still match the pod label;
- the production-safety component is absent from development output.

<details>
<summary>Solution</summary>

Create the component resources:

```bash
cat > "$Work/components/production-safety/pdb.yaml" <<'YAML'
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: web
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: web
YAML

cat > "$Work/components/production-safety/network-policies.yaml" <<'YAML'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-default-deny
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: web
  policyTypes:
    - Ingress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: web-allow-client
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: web
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              access: web-client
      ports:
        - protocol: TCP
          port: 8080
YAML

cat > "$Work/components/production-safety/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
resources:
  - pdb.yaml
  - network-policies.yaml
labels:
  - pairs:
      pod-security.kubernetes.io/enforce: restricted
    includeSelectors: false
YAML
```

Add the component and replacement to the production Kustomization. Keep both patches under one `patches` key; a repeated YAML mapping key can replace or conflict with the earlier value. The complete corrected production Kustomization is:

```bash
cat > "$Work/overlays/prod/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namePrefix: prod-
resources:
  - ../../base
components:
  - ../../components/production-safety
configMapGenerator:
  - name: app-settings
    behavior: merge
    literals:
      - APP_MODE=production
      - LOG_LEVEL=warning
replicas:
  - name: web
    count: 3
replacements:
  - source:
      kind: ConfigMap
      name: app-settings
      fieldPath: data.APP_MODE
    targets:
      - select:
          kind: Deployment
          name: web
        fieldPaths:
          - spec.template.spec.containers.[name=web].env.[name=DEPLOYMENT_TIER].value
patches:
  - target:
      kind: Deployment
      name: web
    patch: |-
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: web
      spec:
        template:
          metadata:
            annotations:
              training.example.com/environment: production
          spec:
            containers:
              - name: web
                envFrom:
                  - configMapRef:
                      name: app-settings
                resources:
                  requests:
                    cpu: 200m
                    memory: 256Mi
  - target:
      kind: Deployment
      name: web
    patch: |-
      - op: add
        path: /metadata/annotations
        value:
          training.example.com/owner: platform-team
YAML
```

The deliberately broken target uses the final transformed name. Patch selection happens against the original resource identity, so the target must be `name: web`. The slash in the annotation key would be escaped as `~1` only when the JSON patch path addresses that key directly. Adding the complete annotations map avoids that escaping requirement here.

Render the final overlays:

```bash
kubectl kustomize "$Work/overlays/dev" > "$Work/dev.yaml"
kubectl kustomize "$Work/overlays/prod" > "$Work/prod.yaml"
```

Use a small structural check instead of relying only on visual inspection:

```bash
python3 - "$Work/dev.yaml" "$Work/prod.yaml" <<'PY'
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit("Install PyYAML for this optional structural check, or inspect the rendered YAML manually.") from exc

dev_path, prod_path = sys.argv[1:]

with open(dev_path, encoding="utf-8") as stream:
    dev = list(yaml.safe_load_all(stream))
with open(prod_path, encoding="utf-8") as stream:
    prod = list(yaml.safe_load_all(stream))

def by_kind(documents, kind):
    return [document for document in documents if document and document.get("kind") == kind]

expected = {
    "Deployment": 1,
    "Service": 1,
    "ConfigMap": 1,
    "PodDisruptionBudget": 1,
    "NetworkPolicy": 2,
}
for kind, count in expected.items():
    actual = len(by_kind(prod, kind))
    assert actual == count, f"{kind}: expected {count}, got {actual}"

deployment = by_kind(prod, "Deployment")[0]
service = by_kind(prod, "Service")[0]
config_map = by_kind(prod, "ConfigMap")[0]
pdb = by_kind(prod, "PodDisruptionBudget")[0]
container = deployment["spec"]["template"]["spec"]["containers"][0]
environment = {item["name"]: item["value"] for item in container["env"]}
config_ref = container["envFrom"][0]["configMapRef"]["name"]
pod_labels = deployment["spec"]["template"]["metadata"]["labels"]

assert deployment["spec"]["replicas"] == 3
assert environment["DEPLOYMENT_TIER"] == "production"
assert config_ref == config_map["metadata"]["name"]
assert service["spec"]["selector"]["app.kubernetes.io/name"] == pod_labels["app.kubernetes.io/name"]
assert pdb["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == pod_labels["app.kubernetes.io/name"]
assert not by_kind(dev, "PodDisruptionBudget")
assert not by_kind(dev, "NetworkPolicy")
print("All Kustomize invariants passed.")
PY
```

If PyYAML is unavailable, do not install it only for this lab unless desired. Inspect with `kubectl kustomize`, `grep`, and `awk`, or use an already approved YAML query tool.

</details>

## Review questions

1. Why should an overlay reference a base instead of copying its rendered YAML?
2. Which changes belong in `images`, `replicas`, generators, patches, replacements, and components?
3. Why can changing selector labels accidentally orphan ReplicaSets or break Service routing?
4. When should generator name suffix hashes remain enabled, and when might a stable generated name be justified?
5. What does successful rendering prove, and what still requires API-schema validation, policy validation, server-side dry-run, or a real rollout?

<details>
<summary>Model answers</summary>

1. A referenced base preserves one source of truth and lets Kustomize recompute overlays after base changes. Copied rendered YAML drifts and loses the transformation intent.
2. Use dedicated transformers for their modeled concerns: `images` for image references, `replicas` for scale, generators for derived ConfigMaps and Secrets, patches for resource-specific structural changes, replacements for copying values between resources, and components for optional reusable resource sets or behavior.
3. Deployments use immutable selectors to own ReplicaSets, while Services route to pods by labels. Broad label transformations that mutate selectors can change ownership or routing semantics. Add organizational labels without including selectors unless selector changes are intentional and reviewed.
4. Keep hashes when configuration changes should create immutable generated objects and update pod references. Disable them only when an external consumer requires a stable name and the rollout/lifecycle consequences are handled explicitly.
5. Rendering proves that Kustomize can assemble syntactically valid YAML from the selected tree. It does not prove that the API server supports every version or field, that admission policies permit the resources, that images run, or that rollout and runtime behavior are correct.

</details>

## Cleanup

Review the path before removing only this disposable folder:

```bash
printf 'Removing %s\n' "$Work"
rm -rf -- "$Work"
unset Work
```

## References

- [Kustomize documentation](https://kubectl.docs.kubernetes.io/guides/)
- [Kubernetes declarative management with Kustomize](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)
- [Kustomize replacements](https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/replacements/)
- [Kustomize components](https://kubectl.docs.kubernetes.io/guides/config_management/components/)

---

**Course complete:** [Return to the lab index](../README.md).
