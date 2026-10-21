# Contributing

Start with the [lab index](README.md), [state map](docs/state.md), and
[operating handbook](docs/operations.md). Labs are cumulative executable teaching material,
not isolated examples or a production landing zone. Preserve numbering gaps and numbered lab URLs.

## Safe local validation

Use Linux, Bash 5+, Python 3.12+, jq, OpenSSL, kubectl (with Kustomize), and Zsh 5.9+.
Azure CLI/Bicep is needed only for local template compilation. No login, subscription,
settings file or cluster is needed for offline validation.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r app/requirements.txt -r tests/requirements.txt
python3 -m unittest discover -s tests -v
(cd app && python3 -m unittest discover -v)
```

Narrow helper changes first, for example:

```bash
python3 -m unittest discover -s tests -k test_acr_token_uses_stdin_and_checks_hostname -v
kubectl kustomize k8s/base >/dev/null
for script in scripts/*.sh ops/*.sh advanced/*.sh infra/*.sh; do bash -n "$script"; zsh -n "$script"; done
for template in infra/*.bicep ops/*.bicep advanced/*.bicep; do
  az bicep build --file "$template" --stdout >/dev/null
done
```

If ShellCheck is installed, run `shellcheck scripts/*.sh ops/*.sh advanced/*.sh`.
Some existing helpers intentionally embed Python; inspect actual behavior rather than only shell
syntax. Tests use temporary directories and fake Azure/Kubernetes commands, with real local
Kustomize where available. A skipped renderer/shell check is not a pass: the PR job supplies
the required tools. Initialized GitOps overlays exist only in configured learner checkouts;
validate those with `kubectl kustomize gitops/clusters/primary/apps/<namespace>`, not the
uninitialized source root that intentionally references generated Flux files.

The `Offline curriculum checks` workflow runs on GitHub-hosted runners with read-only contents,
no OIDC, environment secrets, Azure login, or private network access. It is distinct from the
protected, private self-hosted **publishing** workflow. Never change PR validation to
`pull_request_target` or route untrusted PRs onto that publishing runner.

Do not run deployments, Azure what-if, cleanup, load/fault injection, or cluster mutation as
validation. `deploy-foundation.sh` defaults to what-if, but that still contacts Azure and is
not an offline check. Live execution requires an explicitly approved target and budget.

## Authoring a lab or workshop

Use a visible contract with Entry, Host and identity, Time, Cost, Changes, and Exit and re-entry.
Separate estimated hands-on time from Azure/approval waiting. Keep challenge, required evidence,
stop conditions and mandatory recovery visible; put worked commands in collapsed Solution blocks.
Use the handbook for stable session/review/evidence mechanics. Repeat task-specific safety gates
where the dangerous command appears.

Prefer a focused workshop/extension when it deepens an existing capability. A numbered lab
requires a distinct outcome and explicit resource boundary. Do not fill numbering gaps or
turn optional exercises into hidden prerequisites. End numbered guides with their visible next
lab link, outside collapsed blocks and after references. Workshops return to the index.

Document a new state transition in `docs/state.md`: authoritative source, owner, retained
resources, read-only re-entry proof, recovery and final cleanup. Never reset progressed state
by rerendering, reinitializing GitOps or replaying foundation. Preserve namespace-scoped
reconciliation, dedicated identities, private connectivity, TLS verification and synthetic data.

Use existing helpers and kebab-case options. Normal reviewed delivery lives in
`ops/publish-reviewed-change.sh`; its `advanced/` wrapper is retained for older learners.
Guard expected failures and verify the correct denial, not just any nonzero status. Restore
injected fields explicitly and report recovery errors.

Write host-side command blocks for both Bash 5+ and Zsh 5.9+. Prefer separate `printf`/`read`
prompts, quoted array iteration, `while IFS= read -r` for lines, and JSON selection instead of
shell-indexed API arrays. Do not use Bash's `read -p`, `mapfile`, `BASH_REMATCH` or `${name,,}`
in shared blocks, or assign Zsh's special `status`/`path` parameters. If a facility truly
requires Bash (such as `/dev/tcp`), label it and use an explicit `bash -c` child without losing
the caller's session state. Keep workflow and remote/container interpreter selections explicit.

## Review evidence

Include purpose, affected stages/owners, offline commands/results and known live limitations
in the PR. Add focused tests for new/fixed behavior and update link/contract checks when changing
navigation. The documentation tests parse Bash/Zsh/Python examples, run mocked command gates
under both shells, and verify local links; they
do not establish Azure service support or end-to-end recovery.

Update `docs/operations.md#tooling-and-support`, affected lab support notes and executable pins
together. Keep dependency versions pinned and Actions at full reviewed commit SHAs.
External source reviews and live execution dates are different evidence.

Never commit local settings, rendered files, kubeconfigs, tokens, keys, certificates or private
execution evidence. Use sanitized synthetic fixtures for regression tests.
