# Artifact trust: from digest to admission decision

## Workshop contract

| Field | Contract |
|---|---|
| Entry | Lab 13's digest/SBOM/build metadata concepts and lab 20's admission boundaries. |
| Host and identity | Linux workstation, Python 3.12 and OpenSSL 3; no Azure identity for the required offline exercise. |
| Time | 45-60 minutes active; no Azure wait. |
| Cost | None for local synthetic evidence; real scanning/publishing has separately approved runner/registry costs. |
| Changes | Ignored `.artifacts/trust` only; no cluster policy, registry content or primary workload changes. |
| Exit and re-entry | Valid signed evidence accepted; tampered, unsigned, stale and unacceptable assessments rejected. Record **offline decision rehearsal**, not enforced cluster admission. |

**Challenge:** distinguish an immutable name, an SBOM, vulnerability assessment, provenance
and a trusted admission decision. Lab 13 already produces artifacts; neither an SPDX document
nor unsigned image inspection metadata proves who built the image.

The required exercise uses an explicitly **synthetic** assessment and a local RSA key to
make the cryptographic boundary executable without installing a competing policy stack.
It does not turn self-asserted metadata into verified build provenance or a real scan.
Do not upload the fixture or represent its empty findings as a scanned application image.

## 1. Establish the trust policy before signing

**Task:** name the expected registry digest, repository and authorized builder. Decide how the
platform obtains the public key independently of the release. Explain why accepting a public
key from the same untrusted bundle defeats verification.

**Required evidence:** separate signer/verifier roles, the independently distributed key
fingerprint, exact admitted digest and a 24-hour assessment freshness policy.

<details>
<summary>Solution</summary>

```bash
set -euo pipefail
umask 077
mkdir -p .artifacts/trust
test ! -e .artifacts/trust/signer.key
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out .artifacts/trust/signer.key
openssl pkey -in .artifacts/trust/signer.key -pubout -out .artifacts/trust/trusted.pem
openssl pkey -pubin -in .artifacts/trust/trusted.pem -outform DER | sha256sum
```

This is a disposable local signing key, **not** an Azure credential. In a real pipeline the
signer must be isolated from untrusted PR execution and the verifier's trust root controlled
by the platform, not by application authors. Record the public fingerprint, never the private key.

</details>

## 2. Bind all evidence to one immutable release

**Task:** sign the exact bytes of a record that binds image, source revision, builder and hashes
of the SBOM, build metadata and assessment. Explain which assertions still require trust in the signer.

**Required evidence:** successful cryptographic verification and the exact image/commit admitted.

<details>
<summary>Solution</summary>

The following values are deliberately synthetic, including the digest and commit:

```bash
python3 - <<'PY'
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

root = Path(".artifacts/trust")
image = "example.azurecr.io/order-app@sha256:" + "a" * 64
evidence = {
    "sbom.json": {"spdxVersion": "SPDX-2.3", "name": "SYNTHETIC training SBOM"},
    "metadata.json": {"purpose": "SYNTHETIC offline evidence; not Podman output"},
    "assessment.json": {
        "image": image, "complete": True,
        "assessedAt": datetime.now(timezone.utc).isoformat(),
        "findings": [], "source": "SYNTHETIC training fixture; not a scanner",
    },
}
hashes = {}
for name, value in evidence.items():
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    (root / name).write_bytes(payload)
    hashes[name] = hashlib.sha256(payload).hexdigest()
release = {
    "schema": "aks-lab-release/v1", "image": image,
    "builder": "approved-private-builder", "repository": "training/orders",
    "commit": "b" * 40, "files": hashes,
}
(root / "release.json").write_text(json.dumps(release, sort_keys=True) + "\n")
PY
openssl dgst -sha256 -sign .artifacts/trust/signer.key \
  -out .artifacts/trust/release.sig .artifacts/trust/release.json
Image="example.azurecr.io/order-app@sha256:$(printf 'a%.0s' {1..64})"
python3 ops/verify-release.py --directory .artifacts/trust \
  --public-key .artifacts/trust/trusted.pem --image "$Image" \
  --builder approved-private-builder --repository training/orders
```

The verifier checks the signature **before** trusting the JSON, matches the independently
supplied policy, validates signed artifact hashes, rejects stale/incomplete assessments and
rejects high/critical findings. It emits `allowed:true` only for this local policy. It does
not fetch the OCI manifest or run a scanner; actual production evidence requires those steps.
The expected image above is supplied independently from the synthetic release record. For a real
review, take it from the approved deployment change, not from the bundle being verified.

</details>

## 3. Exercise denial, not just a happy-path signature

**Task:** change the requested digest, then tamper with one artifact. Predict each denial before
running it. Separately show that even a correctly signed assessment can fail freshness or risk policy.

**Required evidence:** each expected nonzero result must include `allowed:false` and the relevant
reason. An unavailable Python/OpenSSL process is not an admission-policy result.

<details>
<summary>Solution</summary>

```bash
Status=0
python3 ops/verify-release.py --directory .artifacts/trust \
  --public-key .artifacts/trust/trusted.pem --image "$Image" \
  --builder unapproved-builder --repository training/orders > .artifacts/trust/denial.json || Status=$?
test "$Status" -eq 1
jq -e '.allowed == false and (.reason | contains("does not match"))' .artifacts/trust/denial.json
cp .artifacts/trust/sbom.json .artifacts/trust/sbom.saved
printf '\n' >> .artifacts/trust/sbom.json
Status=0
python3 ops/verify-release.py --directory .artifacts/trust \
  --public-key .artifacts/trust/trusted.pem --image "$Image" \
  --builder approved-private-builder --repository training/orders > .artifacts/trust/denial.json || Status=$?
test "$Status" -eq 1
jq -e '.allowed == false and (.reason | contains("hash mismatch"))' .artifacts/trust/denial.json
mv .artifacts/trust/sbom.saved .artifacts/trust/sbom.json
python3 -m unittest discover -s tests -p test_release_trust.py -v
```

The regressions create disposable keys and cover missing/untrusted signatures, wrong digests,
tampered artifacts, incomplete/stale assessments and high-severity findings. Unlike simply
breaking a signature, the stale/high cases are **re-signed** to prove that a valid signature
does not bypass admission policy.

</details>

## 4. Design the real enforcement handoff

**Task:** map the same gates onto your approved private publishing and admission system. Do not
install a second Gatekeeper or grant the publishing identity Kubernetes permissions.

**Required evidence:** an implementation decision naming the scanner, provenance format,
trusted builder identity/key rotation, signature/attestation storage, verification policy,
fail-closed/unavailable behavior, scoped expiry-controlled exceptions and rollback process.

<details>
<summary>Model answer</summary>

Build and push once; scan the **pushed digest**, retain the real SBOM and digest-specific complete
assessment, and produce signed provenance in an interoperable format. Keep source/build identity
and artifact subject verifiable, not just untrusted JSON fields. Verify offline/in a review
gate before promoting the same digest to test. A separately operated admission controller must
verify the deployed digest against trusted signatures/attestations and the current risk policy.
Test denied unsigned, tampered, wrong-subject, stale and unauthorized-builder releases in an
isolated namespace before enforcing elsewhere. Define fail-closed behavior with a controlled,
audited emergency exception; a webhook timeout or scan outage is not a clean assessment.

The local verifier is a teaching tool, **not an AdmissionReview server**. It does not secure
your cluster. The current publishing workflow still emits unsigned metadata; cluster-side
signature admission remains an explicitly separate implementation decision, not a hidden
prerequisite or a claimed deployed feature.

</details>

## Cleanup and references

Retain only non-secret decision evidence, then remove the exact disposable key:

```bash
rm -- .artifacts/trust/signer.key
```

Keep/delete the remaining named local fixture files according to your evidence policy;
never commit this directory. There are no cloud resources to tear down.

References: [SLSA provenance](https://slsa.dev/spec/v1.0/provenance),
[OCI image signing with Notation](https://notaryproject.dev/docs/user-guides/how-to/),
[Kubernetes admission](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/).

**Return:** [Lab index](../README.md#lab-index) or [lab 20 governance](../labs/20-governance.md).
