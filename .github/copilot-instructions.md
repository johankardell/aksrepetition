# Copilot instructions

## Repository workflow

- This is a cumulative AKS operations curriculum. Beginner labs 1-6 use the same foundation and hand off to lab 10 without redeployment/rerendering; experienced learners can start fresh at lab 10. Intermediate 10-15 and advanced 20-23 progressively mutate that environment. Labs 24-28 are optional specialists with their own prerequisites; lab 27 is mixed-level and offline. Lab 28 deliberately overlaps an overlay pod IP with a peered service in an isolated disposable environment; never connect it to cumulative/shared networks.
- End every lab with a visible link to the next lab, after references and outside collapsed blocks. Preserve numbering gaps and mark optional transitions; the final lab links back to the README lab index.
- Treat `README.md` and the relevant `labs/<nn>-*.md` as the operational source of truth. Lab command blocks assume Linux, Bash 5+, `jq`, Python 3.12+, and execution from the repository root. Native Bastion supplies cumulative AKS API access; Azure data endpoints still require private connectivity/DNS. All `.sh` helpers also support execution under Zsh 5.9+; only `use-lab.sh`, `activate-aks-bastion.sh`, and `lib.sh` are intended for sourcing.
- Native Bastion tunneling is the primary AKS API path for cumulative labs, with the explicit `aks-preview` exception described in labs 3 and 10. Prefer sourcing `scripts/activate-aks-bastion.sh` for the current shell; `scripts/connect-aks-bastion.sh` retains the preview command's subshell workflow. Both use user credentials, temporary kubeconfigs and verified TLS. Never overwrite an active tunnel kubeconfig with `get-credentials`; Bastion does not provide registry/database/application routing.
- Do not execute billable deployments, destructive cleanup, fault injection, or cluster mutations to validate code. Prefer offline checks unless live execution and the target context are explicitly confirmed.

## Build and validation commands

### Python application

The image uses Python 3.12 and pinned dependencies from `app/requirements.txt`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r app/requirements.txt
(cd app && python3 -m unittest discover -v)
# Single test:
(cd app && python3 -m unittest -v test_app.OrdersTests.test_process_local_deduplication)
podman build --platform linux/amd64 --file app/Containerfile --tag order-app:dev app
```

### Bicep, Kubernetes, and shells

```bash
az bicep build --file infra/main.bicep --stdout >/dev/null
for template in infra/*.bicep ops/*.bicep advanced/*.bicep; do
  az bicep build --file "$template" --stdout >/dev/null
done
kubectl kustomize k8s/base >/dev/null
for script in scripts/*.sh ops/*.sh advanced/*.sh; do bash -n "$script"; done
# When Zsh is installed:
for script in scripts/*.sh ops/*.sh advanced/*.sh; do zsh -n "$script"; done
```

Validate changed initialized GitOps overlays with `kubectl kustomize gitops/clusters/primary/apps/<namespace>`. Generated overlays exist only in configured checkouts. Run `shellcheck scripts/*.sh ops/*.sh advanced/*.sh` when ShellCheck is available.

Run offline helper regressions with `python3 -m unittest discover -s tests -v`; a single test is `python3 -m unittest discover -s tests -k test_acr_token_uses_stdin_and_checks_hostname -v`. These tests use synthetic contexts and mocked Azure/cluster commands, running helper behavior under both Bash and Zsh when Zsh is installed.

`bash scripts/deploy-foundation.sh` performs Azure what-if only. Applying requires `--apply --confirm` and an explicit resource-group confirmation.

## Architecture

- `infra/main.bicep` bootstraps private AKS, VNet/subnets, Bastion, a private Ubuntu build VM, the shared firewall hub/peering, separate cluster/kubelet identities, ACR, Log Analytics, Key Vault, Service Bus, and federated workload identities. Focused templates introduce later capabilities.
- `infra/bastion.bicep` is the foundation's `bastion` module: Standard SKU, two scale units and a public IP. `infra/build-vm.bicep` creates an Ubuntu 24.04 Standard_B2s host with a 64-GiB Standard SSD, separate SSH key, no public IP/managed-identity privileges, and Bastion-only inbound SSH. Cloud-init configures rootless Podman and lab tools; the readiness extension fails deployment on setup errors. The routed management subnet uses firewall egress from foundation; lab 12 adds only node UDR/outbound-type changes. Do not add a VPN or weaken private data endpoints.
- Firewall creation precedes the spoke (its management route references the firewall output); shared peering follows both, and VM startup follows peering. Preserve this ordering and the `deployPeering: false` foundation call to avoid a deployment cycle. Bastion's subnet never inherits the management/node UDR. Regional foundations must set both spoke and hub prefixes and pass the separate build-VM public key.
- `app/app.py` serves two roles in one image selected by `ROLE`: FastAPI producer and Service Bus worker. Each has its own service account, managed identity, and queue-scoped role. Deduplication is process-local before lab 21; PostgreSQL adds durable idempotency/lookup in lab 21.
- `scripts/render-manifests.sh` resolves `k8s/base` into ignored `rendered/base`. Labs 11-12 accumulate CSI, identity, and networking changes there.
- Lab 13 transfers the accumulated state once through `ops/initialize-gitops.sh` into primary `orders`/`orders-test` application roots. The platform root owns namespaces/RBAC; namespace-scoped Flux owns only allowed app resources.
- Delivery, monitoring, and scaling belong under `ops/`; governance, data recovery, upgrades, and regional recovery under `advanced/`. The cold regional overlay derives from the stable base and deliberately excludes HPA/KEDA.

## Repository-specific conventions

- Source `scripts/use-lab.sh` for lab state. `Lab` and `Outputs` are JSON strings; use `lab_value KEY`, `output_value KEY`, or `jq`, not object-property shell syntax. The helper validates identifiers and active subscription/tenant without switching context. Generated names come from `foundation` outputs, never guessed suffixes.
- Script filenames use lowercase kebab-case. Execute other helpers with `bash path/name.sh --kebab-case-option value` or `zsh path/name.sh --kebab-case-option value`. Source `use-lab.sh` and `activate-aks-bastion.sh`; `lib.sh` is the shared sourced library. Shared argument handling and validation live in `scripts/lib.sh`; executable helpers use `set -euo pipefail`, quoted paths, and explicit error reporting. Sourced helpers preserve caller shell options; keep new shell logic compatible with both Bash and Zsh.
- Keep source manifests deployment-independent using `__UPPERCASE_TOKEN__`. Rendering must replace all tokens, fail on unresolved values, and validate the resulting Kustomize tree.
- Keep local settings, `rendered/`, `.artifacts/`, `evidence/`, keys, certificates, and kubeconfigs uncommitted.
- `infra/main.bicep` is an initial bootstrap snapshot. Never redeploy it over a progressed primary cluster: later labs change networking, add-ons, public access, scaling, and versions.
- Before Flux adoption, preserve accumulated rendered changes. After adoption, edit Git sources or explicitly suspend/resume the named reconciler for controlled faults. Never rerun rendering or GitOps initialization.
- Promote immutable `sha256:` digests with `ops/set-gitops-image.sh`. The private self-hosted publishing runner builds once with Podman and retains the digest, build metadata, and SPDX SBOM; metadata is not signed provenance.
- Authenticate Podman with `scripts/connect-acr-podman.sh`: obtain a short-lived Entra token, verify the returned registry hostname, and send the token through standard input. Do not store Azure passwords.
- Keep autoscalers/scaler identities out of `k8s/base`; use `ops/scaling`. Preserve the distinct API, worker, scaler, publisher, Flux, operator, and test permission boundaries.
- Preserve workload UID/GID 10001, non-root/read-only-root-filesystem, dropped capabilities, resource bounds, and explicit writable mounts.
- Preserve full-SHA Actions pins with reviewed dates and the private self-hosted runner requirement.
