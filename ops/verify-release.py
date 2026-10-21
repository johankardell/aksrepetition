"""Offline artifact-trust decision exercise; not a deployed admission webhook."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def verify(directory, public_key, image, builder, repository, now=None):
    now = now or datetime.now(timezone.utc)
    if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Require the exact registry/repository@sha256 digest")
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(public_key),
         "-signature", str(directory / "release.sig"), str(directory / "release.json")],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        raise ValueError("Missing or invalid trusted release signature")
    release = json.loads((directory / "release.json").read_text())
    if not isinstance(release, dict) or release.get("schema") != "aks-lab-release/v1":
        raise ValueError("Unsupported release evidence schema")
    if (release.get("image"), release.get("builder"), release.get("repository")) != (
        image, builder, repository
    ):
        raise ValueError("Image, builder or repository does not match admission policy")
    if not re.fullmatch(r"[a-f0-9]{40}", release.get("commit", "")):
        raise ValueError("Require a full reviewed Git commit SHA")
    files = release.get("files")
    required = {"sbom.json", "metadata.json", "assessment.json"}
    if not isinstance(files, dict) or set(files) != required:
        raise ValueError("Require exactly the SBOM, build metadata and assessment")
    evidence = {}
    for name in sorted(required):
        payload = (directory / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != files[name]:
            raise ValueError(f"Signed hash mismatch: {name}")
        evidence[name] = json.loads(payload)
        if not isinstance(evidence[name], dict):
            raise ValueError(f"Expected an evidence object: {name}")
    if not re.fullmatch(r"SPDX-2[.][0-9]+", evidence["sbom.json"].get("spdxVersion", "")):
        raise ValueError("Require SPDX 2.x evidence")
    assessment = evidence["assessment.json"]
    if assessment.get("image") != image or assessment.get("complete") is not True:
        raise ValueError("Assessment must be complete for the admitted digest")
    assessed = datetime.fromisoformat(assessment["assessedAt"].replace("Z", "+00:00"))
    if assessed.tzinfo is None or not now - timedelta(hours=24) <= assessed <= now + timedelta(minutes=5):
        raise ValueError("Assessment is stale, undated or in the future")
    findings = assessment.get("findings")
    if not isinstance(findings, list):
        raise ValueError("Assessment findings must be an explicit list")
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("severity") not in {
            "critical", "high", "medium", "low", "none"
        }:
            raise ValueError("Unknown assessment finding/severity")
        if finding["severity"] in {"critical", "high"}:
            raise ValueError("Admission policy rejects high/critical findings")
    return {"allowed": True, "image": image, "commit": release["commit"],
            "reason": "Trusted evidence satisfies the local exercise policy"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--builder", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        decision = verify(args.directory, args.public_key, args.image, args.builder, args.repository)
    except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"allowed": False, "reason": str(error)}))
        return 1
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
