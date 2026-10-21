from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_release", ROOT / "ops/verify-release.py")
release_verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_verifier)


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class ReleaseTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "key"
        self.public = self.root / "trusted.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                        "rsa_keygen_bits:2048", "-out", str(self.key)],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.key), "-pubout", "-out", str(self.public)],
                       check=True, capture_output=True)
        self.now = datetime.now(timezone.utc)
        self.image = "example.azurecr.io/order-app@sha256:" + "a" * 64
        self.assessment = {"image": self.image, "complete": True,
                           "assessedAt": self.now.isoformat(), "findings": []}
        self.sign()

    def sign(self):
        payloads = {"assessment.json": self.assessment, "sbom.json": {"spdxVersion": "SPDX-2.3"},
                    "metadata.json": {"synthetic": True}}
        hashes = {}
        for name, value in payloads.items():
            content = json.dumps(value).encode()
            (self.root / name).write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        record = {"schema": "aks-lab-release/v1", "image": self.image, "builder": "builder",
                  "repository": "test/repo", "commit": "b" * 40, "files": hashes}
        (self.root / "release.json").write_text(json.dumps(record))
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.key),
                        "-out", str(self.root / "release.sig"), str(self.root / "release.json")],
                       check=True, capture_output=True)

    def verify(self, image=None):
        return release_verifier.verify(self.root, self.public, image or self.image,
                                       "builder", "test/repo", now=self.now)

    def test_signed_digest_binding_and_tamper_denial(self):
        self.assertTrue(self.verify()["allowed"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.verify(self.image[:-1] + "c")
        (self.root / "sbom.json").write_text('{"spdxVersion":"SPDX-2.3","tampered":true}')
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.verify()

    def test_signed_but_unacceptable_assessment(self):
        cases = [
            ({"complete": False}, "complete"),
            ({"assessedAt": (self.now - timedelta(days=2)).isoformat()}, "stale"),
            ({"assessedAt": (self.now + timedelta(hours=1)).isoformat()}, "future"),
            ({"findings": [{"severity": "high"}]}, "high/critical"),
            ({"findings": [{"severity": "unknown"}]}, "Unknown"),
        ]
        original = dict(self.assessment)
        for changes, reason in cases:
            with self.subTest(changes=changes):
                self.assessment = dict(original, **changes)
                self.sign()
                with self.assertRaisesRegex(ValueError, reason):
                    self.verify()

    def test_missing_or_untrusted_signature_denies(self):
        signature = self.root / "release.sig"
        signature.unlink()
        with self.assertRaisesRegex(ValueError, "signature"):
            self.verify()
        self.sign()
        signature.write_bytes(b"not a trusted signature")
        with self.assertRaisesRegex(ValueError, "signature"):
            self.verify()
        self.sign()
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                        "rsa_keygen_bits:2048", "-out", str(self.root / "other.key")],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.root / "other.key"),
                        "-pubout", "-out", str(self.public)], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "signature"):
            self.verify()

    def test_command_reports_denial_as_json(self):
        (self.root / "release.sig").unlink()
        result = subprocess.run(
            ["python3", str(ROOT / "ops/verify-release.py"), "--directory", str(self.root),
             "--public-key", str(self.public), "--image", self.image,
             "--builder", "builder", "--repository", "test/repo"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["allowed"])
        self.assertIn("signature", json.loads(result.stdout)["reason"])


if __name__ == "__main__":
    unittest.main()
