# Optional workshop — Authenticate and authorize the test orders API

## Workshop contract

- **Entry point:** after [lab 13](../labs/13-gitops.md), before returning to [lab 14](../labs/14-observability.md). This is optional, not a new cumulative lab. Follow [operations](../docs/operations.md) and [state/ownership](../docs/state.md).
- **Scope/owner:** the application owner changes only the `orders-test` API Deployment through reviewed Git. The primary `orders` API, worker identities, Service Bus queue permissions, autoscaling, database semantics and cumulative synthetic unauthenticated default remain unchanged.
- **Prerequisites:** working namespace-scoped Flux, the lab 13 test identities and `orders-test` queue, an immutable image containing this implementation, and the existing private management/API path. Never rerender or reinitialize GitOps. Use Linux/Bash from the repository root, the supported Python environment and its `app/requirements.txt`.
- **Authority:** app-registration ownership and delegated-consent authority in the Entra directory are separate from Azure subscription, AKS RBAC, Workload ID and Git review rights. A directory administrator may need to register/approve the applications or grant consent. Do not grant Global Administrator, broad Graph permissions or subscription roles to make this exercise work.
- **Cost/risk:** offline tests deploy nothing. Optional live execution reuses the test API/queue and existing publisher runner; image publication/storage, replica execution and messages have their normal costs. `--write` below explicitly sends one synthetic message. Do not expose this test API through a public gateway.
- **Exit/restore:** retain sanitized test/status evidence and reviewed change/removal PRs, remove the authentication patch through review, restore the previous test image digest, and confirm primary desired state is unchanged.

## Authentication and endpoint contract

Authentication is **off by default**. Only the API role reads these settings at process startup; workers do not initialize the API JWT verifier. Changing settings requires an API rollout.

| Variable | Enabled value |
| --- | --- |
| `AUTH_ENABLED` | Exactly `true`; unset or exactly `false` preserves the existing lab API. Any other value fails API startup. |
| `AUTH_TENANT_ID` | Your single Entra tenant UUID, not `common`, `organizations`, or a domain alias. |
| `AUTH_AUDIENCE` | API application's client-ID UUID, **not** `api://...`, the interactive client ID, ARM or Microsoft Graph. |
| `AUTH_ISSUER` | `https://login.microsoftonline.com/TENANT_UUID/v2.0` |
| `AUTH_JWKS_URL` | `https://login.microsoftonline.com/TENANT_UUID/discovery/v2.0/keys` |

This implementation deliberately supports public-cloud Entra v2 access tokens only. It pins the tenant-specific issuer and JWKS endpoint, verifies TLS without redirects, accepts only RS256, validates signature/issuer/audience/expiry/not-before/issued-at, and requires `sub`, `tid`, and `ver=2.0`. It does not trust unverified claims or token-supplied key URLs. Missing/invalid enabled configuration fails startup, never anonymous fallback. A missing/invalid token returns **401** with a Bearer challenge; a validated token lacking permission returns **403**; an unavailable JWKS provider returns **503** when a fresh key set is needed. Verified cached keys may serve valid tokens for up to five minutes during an outage; that is not an anonymous fallback. JWKS requests have a five-second timeout and a bounded response.

| Endpoint | With authentication enabled |
| --- | --- |
| `GET /`, `GET /orders/{id}`, `GET /config-version` | Delegated scope `orders.read` **or** application role `Orders.Read` |
| `POST /orders` | Delegated scope `orders.write` **or** application role `Orders.Write` |
| `GET /healthz`, `GET /readyz` | Anonymous Kubernetes probes, unchanged readiness-fault behavior; no dependency reachability claim |
| `GET /metrics` | Anonymous internal Prometheus scrape; aggregate request status/latency, no bearer or subject labels |
| `/docs`, `/redoc`, `/openapi.json` | Disabled (404), not an unauthenticated API explorer/schema |

Write does **not** imply read. `GET /orders/{id}` still returns 503 before lab 21 enables durable storage, even for an authorized reader; authorization does not implement database lookup earlier. `/config-version` remains synthetic-only and normally returns 503 in `orders-test`, which intentionally has no CSI mount or Key Vault role. Do not point it at a real secret.

**Exposure boundary:** this is application authorization, not complete production API hardening. Keep the test Service internal and access it only by an operator-authorized loopback port-forward. Kubernetes RBAC controls that tunnel. Probes and metrics intentionally do not require bearer tokens: retain network policy/private routing, and never route them through a public listener. No new ingress/HTTPRoute belongs to this exercise. If adapting it for externally reachable services, separately design authenticated monitoring, edge path restrictions/rate limits, audit policy and abuse protection. Preserve existing metric names/labels, and configure proxies/APM not to capture `Authorization`, request tokens or user claims. Do not enable HTTP/identity debug logging.

## Task 1 — Prove authorization offline before directory changes

**Challenge:** distinguish 401, 403 and provider failure from accepted business requests, and prove rejected writes never reach Service Bus. A fake unsigned token or an unverified decode is not a test of authentication.

**Required evidence:** sanitized test summary showing signature/issuer/audience/expiry rejection, read-only access with forbidden writes, accepted writes, provider outage, default-off compatibility and worker isolation. No tokens, private test keys, real user claims or cloud access are evidence.

<details>
<summary>Solution — generated local signing keys, mocked side effects</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r app/requirements.txt
(cd app && python3 -m unittest -v test_app test_authentication test_authentication_probe)
```

`test_authentication.py` generates ephemeral RSA keys in memory and supplies a mocked JWKS response. Its claims use synthetic tenant/audience values and an Entra-shaped issuer string: **these are locally signed fixtures, not real Entra access tokens or an Entra integration test**. The full ASGI routes run with mocked Service Bus/database clients. Read succeeds, forbidden write returns 403 without invoking the sender, and authorized write invokes the mocked sender. Wrong signature/issuer/audience/expiry and unsigned tokens return 401. JWKS outage returns 503 and is counted by the existing HTTP metrics. Anonymous health/readiness/metrics remain available. `test_authentication_probe.py` mocks device sign-in and HTTP to verify scope selection, token-free output and refusal of remote targets/redirects. No live Service Bus, database, Entra or Azure calls occur.

Pinned `PyJWT[crypto]` and `cryptography` release metadata was checked against public PyPI on 2026-10-21. The image copies the verifier module as well as `app.py`; use the existing reviewed private-runner publication workflow, not a local unreviewed image tag.

</details>

## Task 2 — Obtain narrowly scoped directory approval

**Challenge:** configure a single-tenant API and a dedicated public client that can obtain only a read permission. Explain why cluster-admin, a Service Bus sender role and an AKS Workload ID token do not authorize end-user API calls.

**Required evidence:** API/client application IDs, tenant, configured token version, scope names, approval/consent decision and named owners. Record metadata only, not bearer tokens, device codes or browser screenshots containing credentials.

<details>
<summary>Solution — precise Entra portal registration and consent</summary>

Have the authorized directory owner perform these steps in **Microsoft Entra admin center → Identity → Applications → App registrations**:

1. **New registration:** name `orders-test-api`, choose **Accounts in this organizational directory only**, no redirect URI. Record its **Application (client) ID** and **Directory (tenant) ID**. Under **Manifest**, set `api.requestedAccessTokenVersion` to `2` (retain existing unrelated manifest fields). This resource setting controls access-token format; choosing a v2 authorization endpoint alone is not sufficient.
2. **Expose an API:** set Application ID URI to `api://API_CLIENT_ID`. Add enabled delegated scopes `orders.read` and `orders.write`, with explicit descriptions (“Read synthetic test orders” and “Enqueue synthetic test orders”). Use **Admins only** consent for this controlled exercise; obtain approval from the tenant's authorized consent administrator rather than widening learner roles. No Graph/ARM application permission is required.
3. **New registration:** single-tenant `orders-test-read-client`, no secret/certificate and no redirect URI. Under **Authentication → Advanced settings**, enable **Allow public client flows** for device-code sign-in. Under **API permissions → Add a permission → My APIs → orders-test-api → Delegated permissions**, select **only** `orders.read`; remove an unneeded default Graph `User.Read` permission if present. The authorized approver grants tenant consent to this permission. Do not add `orders.write` or preauthorize this client for write.
4. If you elect to send a live synthetic order, create a **different** single-tenant `orders-test-write-client`, also public/device-code enabled with no secret. Request and approve only the API's delegated `orders.read` and `orders.write` permissions. Keeping clients separate prevents previously consented write scopes from undermining the read-only demonstration.
5. Optional application-to-application design discussion: under the **API registration → App roles**, define `Orders.Read` and `Orders.Write` with allowed member type **Applications**. Assignment/admin consent to an approved caller service principal is a separate privileged change, **not needed for this delegated-user workshop**. Generated offline tests cover these role claims; this workshop does not claim a live app-only credential test.

Do not change the existing producer/worker Workload ID registrations or queue roles. Do not request a token using the Azure CLI's first-party client for an arbitrary custom API: that requires a separate preauthorization relationship and is not this flow. The helper below uses your dedicated approved public client and the exact `api://API_CLIENT_ID/orders.read` scope, not `.default`, ARM or Graph.

Device-code flow may be blocked by tenant Conditional Access; stop and request an approved interactive-client design from the identity owner. Do not bypass policy, create a client secret, or grant broad directory permissions.

</details>

## Task 3 — Opt in only the test API through reviewed Git

**Challenge:** prove the proposed rendered diff changes test API authentication only, without replacing primary identities, networking, accumulated patches or queue ownership.

**Required evidence:** reviewed PR and image-publishing run/artifact reference tied to the authentication source commit (not signed provenance), old/new immutable test digest, rendered test API settings, unchanged primary tree, Flux readiness and test API rollout. A rollout alone is **not** authentication success: task 4's benign anonymous-401/authenticated-200 gate is mandatory. Environment values are public identifiers, not secrets. Never capture credentials.

<details>
<summary>Solution — explicit namespace-scoped patch</summary>

**Build/publish before promotion.** First merge the reviewed application implementation into `main`, including `app/authentication.py`, the updated `app/app.py` and pinned requirements, and the `app/Containerfile` instruction that copies **both** Python modules. A previously published cumulative image may ignore `AUTH_ENABLED` entirely; setting environment variables or observing healthy pods does not prove that authentication exists in that image. Do not reuse its digest for opt-in.

From the clean, updated reviewed `main` checkout, use the existing lab 13 private-runner workflow to build and publish that source commit. The protected environment's approval and private ACR connectivity remain required. The following commands are an **optional live publishing operation**, not part of offline validation:

```bash
set -euo pipefail
source ./scripts/use-lab.sh
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
AuthSourceCommit=$(git rev-parse HEAD)
git show "$AuthSourceCommit":app/authentication.py >/dev/null
git show "$AuthSourceCommit":app/Containerfile | grep -Fx 'COPY app.py authentication.py .'
printf '%s' 'Existing private GitHub repository (owner/name): ' >&2
read -r GitHubRepository
gh workflow run publish-image.yml --repo "$GitHubRepository" --ref main
gh run list --repo "$GitHubRepository" --workflow publish-image.yml --limit 5
printf '%s' 'Publishing run ID for the reviewed source commit: ' >&2
read -r AuthRunId
[[ "$AuthRunId" =~ ^[0-9]+$ ]]
gh run watch "$AuthRunId" --repo "$GitHubRepository" --exit-status
gh run view "$AuthRunId" --repo "$GitHubRepository" \
  --json headSha,conclusion,workflowName \
  | jq -e --arg commit "$AuthSourceCommit" \
    '.headSha == $commit and .conclusion == "success" and .workflowName == "Publish private order image"'
AuthArtifactDirectory=".artifacts/authentication/image/$AuthRunId"
mkdir -p "$AuthArtifactDirectory"
gh run download "$AuthRunId" --repo "$GitHubRepository" \
  --name "order-image-$AuthSourceCommit" --dir "$AuthArtifactDirectory"
AuthReference=$(< "$AuthArtifactDirectory/image-reference.txt")
AuthReference=${AuthReference%$'\r'}
AuthDigest=${AuthReference##*@}
[[ "$AuthDigest" =~ ^sha256:[a-f0-9]{64}$ ]]
test "$AuthReference" = "$(lab_value RegistryServer)/order-app@$AuthDigest"
printf 'Reviewed source: %s\nPublishing run: %s\nTest image: %s\n' \
  "$AuthSourceCommit" "$AuthRunId" "$AuthReference"
```

If that exact reviewed commit already has a successful lab 13 publishing run, select and validate that run instead of dispatching a duplicate build; still perform the source/run/artifact checks above. Retain its digest, build metadata and SBOM without claiming signed provenance. Publication alone changes neither application namespace. Promote the validated digest **only** to `orders-test` below. Use the active Bastion-backed kubeconfig; do not overwrite it with `get-credentials`.

```bash
set -euo pipefail
source ./scripts/use-lab.sh
git status --short
# Start from a clean, reviewed main checkout with initialized GitOps sources.
kubectl kustomize gitops/clusters/primary/apps/orders-test >/dev/null
PrimaryBefore=$(git rev-parse HEAD:gitops/clusters/primary/apps/orders)
printf '%s' 'Previous orders-test image digest (sha256:...; record for restore): ' >&2
read -r PreviousDigest
: "${AuthDigest:?Complete the reviewed source/run/artifact checks before test-only promotion}"
printf '%s' 'Approved directory tenant UUID: ' >&2
read -r AuthTenant
printf '%s' 'API application client-ID UUID: ' >&2
read -r AuthAudience
export AuthTenant AuthAudience
mkdir -p .artifacts/authentication
python3 - <<'PY'
import json, os, pathlib, uuid
tenant = str(uuid.UUID(os.environ["AuthTenant"]))
audience = str(uuid.UUID(os.environ["AuthAudience"]))
values = {
    "AUTH_ENABLED": "true",
    "AUTH_TENANT_ID": tenant,
    "AUTH_AUDIENCE": audience,
    "AUTH_ISSUER": f"https://login.microsoftonline.com/{tenant}/v2.0",
    "AUTH_JWKS_URL": f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
}
patch = {
    "apiVersion": "apps/v1", "kind": "Deployment",
    "metadata": {"name": "order-api", "namespace": "orders-test"},
    "spec": {"template": {"spec": {"containers": [{
        "name": "api",
        "env": [{"name": key, "value": value} for key, value in values.items()],
    }]}}},
}
# JSON is valid YAML; no template tokens or shell interpolation ambiguity.
pathlib.Path(".artifacts/authentication/api-authentication-patch.yaml").write_text(
    json.dumps(patch, indent=2) + "\n"
)
PY
bash ./ops/set-gitops-image.sh --namespace orders-test --digest "$AuthDigest"
bash ./ops/add-gitops-file.sh --namespace orders-test --kind Patch \
  --source .artifacts/authentication/api-authentication-patch.yaml
kubectl kustomize gitops/clusters/primary/apps/orders-test
git diff -- gitops/clusters/primary/apps/orders-test
git diff --exit-code -- gitops/clusters/primary/apps/orders
git add gitops/clusters/primary/apps/orders-test/kustomization.yaml \
  gitops/clusters/primary/apps/orders-test/api-authentication-patch.yaml
bash ./ops/publish-reviewed-change.sh --message "Opt test API into scoped Entra authorization"
test "$PrimaryBefore" = "$(git rev-parse HEAD:gitops/clusters/primary/apps/orders)"
flux reconcile kustomization orders-test -n flux-system --with-source
kubectl rollout status deployment/order-api -n orders-test --timeout=180s
```

Confirm the previous digest against the existing test `kustomization.yaml` **before** modifying it; retain it in your sanitized operational notes for a later shell. `set-gitops-image.sh` promotes the common test image used by API and worker; auth settings are attached only to container `api` in `order-api`, never shared `orders-config` or the worker. The worker keeps the exact existing authorization behavior.

**Existing egress boundary:** lab 13 copies the accumulated [`k8s/network/policies.yaml`](../k8s/network/policies.yaml) into `orders-test` through `initialize-gitops.sh`. Its `required-dns-and-https` policy permits DNS to `kube-system` on UDP/TCP 53 and outbound TCP 443; its default-deny policy remains in effect. The L4 HTTPS rule is **not** a destination allowlist. [`infra/firewall.bicep`](../infra/firewall.bicep)'s `identity-gitops-observability` / `required-https` application rule permits the public-cloud authority `login.microsoftonline.com` from `nodesAddress`, covering the tenant's `/discovery/v2.0/keys` endpoint. The `build-management` rule separately permits the authority from the management subnet for interactive sign-in. Thus the authored cumulative baseline already provides this path: **no new NetworkPolicy or firewall allowance is required for the workshop**.

Inspect the initialized test GitOps policy and the current approved network configuration for drift before claiming that the deployed path matches the baseline. The Azure Firewall rule is shared/node-subnet scoped, not an `orders-test` identity boundary; neither it nor Kubernetes L4 policy enforces a tenant URL path. The verifier enforces the exact tenant issuer/key URL. “Test-only” means the authentication rollout/requests are confined to `orders-test`, not that this exercise creates separate network infrastructure.

If an operator has narrowed the baseline, stop for that network owner's review: preserve DNS and allow only the necessary API egress to the existing authority on HTTPS 443, using workload-scoped policy where the network implementation supports it. Any corresponding firewall change belongs to the network owner outside this application workshop; do not redeploy foundation, broadly open egress or silently modify primary/network policy. Do not disable TLS verification, use a `common` issuer or copy JWKS keys into Git to hide an outage. No JWKS fetch occurs merely to satisfy readiness: readiness remains the original shallow probe. Invalid enabled settings prevent startup; valid settings with an unreachable provider produce 503 on protected requests. Diagnose sanitized status/configuration only, not bearer contents.

</details>

## Task 4 — Demonstrate real Entra enforcement without leaking tokens

**Challenge:** use the approved read-only client to prove a real access token permits read but cannot enqueue. Keep tokens out of command arguments, shell variables, traces, evidence, Git and persistent caches. Separately decide whether to send a live synthetic order.

**Required evidence:** token-free status lines (`401`, read `200`, write `403`, probe `200`, explorer/schema `404`), namespace/tunnel target and unchanged primary tree. For the optional writer, retain the synthetic order ID and test-worker processing evidence; `202` alone does not prove completion.

<details>
<summary>Solution — in-memory device sign-in and loopback probe</summary>

In one terminal with the active authorized kubeconfig, keep this foreground tunnel running:

```bash
kubectl port-forward --address 127.0.0.1 -n orders-test service/order-api 18080:80
```

Check its printed target, and ensure no stale tunnel owns port 18080. Never use `--address 0.0.0.0`, and do not point the helper at the primary gateway. In a second terminal on the same management host:

```bash
source .venv/bin/activate
printf '%s' 'Approved tenant UUID: ' >&2
read -r AuthTenant
printf '%s' 'API application client-ID UUID: ' >&2
read -r AuthAudience
printf '%s' 'Read-only public client-ID UUID: ' >&2
read -r ReadClient
python3 ops/authentication/probe.py \
  --tenant-id "$AuthTenant" --api-client-id "$AuthAudience" --client-id "$ReadClient"
```

The helper uses MSAL (already required by `azure-identity`), requests only the expected API scope, and keeps the token cache in memory. Follow its device sign-in instruction in a trusted browser. Do not record the device code or full terminal transcript; retain only the subsequent method/path/status lines. Neither the token nor response bodies are printed, and no token appears in subprocess arguments or files. The HTTP bearer hop is **loopback only**, carried to AKS over the existing authenticated port-forward transport; the helper refuses proxies and redirects. This is not permission to send bearer tokens over an ordinary network's plaintext HTTP.

**Mandatory benign activation gate:** before any write attempt or success claim, the helper first checks anonymous `GET /` = **401**, then a real Entra read-token `GET /` = **200**. This route has no queue/database side effects. It stops on either unexpected status before reaching `POST /orders`. Together these results prove the deployed image loaded authentication and completed signature/issuer/audience/permission verification, including usable JWKS keys; healthy probes, an environment-variable listing or a rollout alone prove none of those things. In particular, an old unauthenticated image returns 200 to the first anonymous request and fails this gate immediately.

Only after that gate does the helper attempt the read-only `/orders` write, expecting 403 (no enqueue), then probes = 200 and docs/schema = 404. Treat the workshop as successful only after **all** checks pass. An unexpected 202 is a failure of your read-only registration/consent boundary: stop, review that client's granted API scopes/roles, and inspect the test queue. Do not decode or paste the token into a web debugger.

**Optional live mutation:** only with the distinct approved writer client, explicit approval to use the lab's `orders-test` queue, and confirmation of the API's `QUEUE_NAME=orders-test` plus the distinct test identities:

```bash
printf '%s' 'Separate approved writer public client-ID UUID: ' >&2
read -r WriteClient
python3 ops/authentication/probe.py --write \
  --tenant-id "$AuthTenant" --api-client-id "$AuthAudience" --client-id "$WriteClient"
kubectl logs -n orders-test deployment/order-worker --since=5m
```

Filter your evidence to the printed synthetic order ID and the matching `order_processed` record. Before lab 21, processing/deduplication remains process-local and durable `GET /orders/{id}` is unavailable; do not “fix” that by changing the database or granting primary queue permissions.

</details>

## Task 5 — Restore the cumulative learning path

**Challenge:** remove the optional authentication setting through the same reviewed Git path, restore the prior test image, and prove no manual rollout/reconciler suspension was left behind.

**Required evidence:** reviewed removal PR, restored test digest, no authentication environment patch in the rendered test Deployment, anonymous synthetic API restored, both app reconciliations healthy and unchanged primary tree.

<details>
<summary>Solution — reviewed removal, not a live kubectl patch</summary>

Stop the port-forward with Ctrl-C. From the clean updated main checkout:

```bash
set -euo pipefail
: "${PreviousDigest:?Restore the recorded pre-workshop orders-test digest}"
: "${PrimaryBefore:?Restore the recorded pre-workshop primary Git tree ID}"
bash ./ops/remove-gitops-file.sh --namespace orders-test --name api-authentication-patch.yaml
bash ./ops/set-gitops-image.sh --namespace orders-test --digest "$PreviousDigest"
kubectl kustomize gitops/clusters/primary/apps/orders-test
git diff --exit-code -- gitops/clusters/primary/apps/orders
git add gitops/clusters/primary/apps/orders-test/kustomization.yaml \
  gitops/clusters/primary/apps/orders-test/api-authentication-patch.yaml
bash ./ops/publish-reviewed-change.sh --message "Restore test API after optional authentication workshop"
flux reconcile kustomization orders-test -n flux-system --with-source
kubectl rollout status deployment/order-api -n orders-test --timeout=180s
flux get kustomizations -n flux-system
test "$PrimaryBefore" = "$(git rev-parse HEAD:gitops/clusters/primary/apps/orders)"
```

Briefly restart the **same `orders-test` loopback port-forward** and run `curl --fail --silent http://127.0.0.1:18080/ >/dev/null` without credentials; then stop it. The restored API permits the original synthetic anonymous behavior, not real data exposure. Remove the locally generated `.artifacts/authentication/api-authentication-patch.yaml`. Have the directory owner revoke workshop client consent and remove only the workshop-owned registrations if no longer needed. Do not delete shared registrations, test queue/identities or curriculum GitOps roots. Closing the probe process discards its in-memory tokens; do not create token files for cleanup evidence.

</details>

## References

- [Microsoft identity platform access-token validation](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens#validate-tokens)
- [Expose a web API's scopes](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-expose-web-apis)
- [Configure a client to access a web API](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis)
- [Device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Application manifest reference](https://learn.microsoft.com/en-us/entra/identity-platform/reference-app-manifest)
- [Operations contract](../docs/operations.md) · [State and ownership](../docs/state.md)

**Next recommended:** return to [lab 14 — Observability](../labs/14-observability.md), or return to the [README lab index](../README.md#lab-index). This optional workshop does not change lab numbering.
