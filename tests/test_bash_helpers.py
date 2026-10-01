import http.server
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
ADMIN = "33333333-3333-3333-3333-333333333333"


class BashHelpersTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        for directory in ("scripts", "ops", "advanced", "k8s", "infra", "gitops"):
            shutil.copytree(ROOT / directory, self.root / directory)
        self.settings = {
            "SubscriptionId": SUBSCRIPTION, "TenantId": TENANT,
            "AdminGroupObjectId": ADMIN, "Prefix": "testlab", "Namespace": "orders",
            "ImageTag": "v1", "ResourceGroup": "test-rg", "Location": "test-region",
            "KubernetesVersion": "1.34.1", "VmSize": "Standard_D4ds_v5",
        }
        self.outputs = {key: {"value": value} for key, value in {
            "acrName": "testregistry", "registryServer": "testregistry.azurecr.io",
            "keyVaultName": "generated-vault", "serviceBusName": "generated-bus",
            "apiClientId": "api-client", "workerClientId": "worker-client",
        }.items()}
        (self.root / "local.settings.json").write_text(json.dumps(self.settings))
        (self.root / "outputs.json").write_text(json.dumps(self.outputs))
        binary = self.root / "bin"
        binary.mkdir()
        self.env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}",
                        TEST_ROOT=str(self.root), TEST_SUBSCRIPTION=SUBSCRIPTION)
        self.mock(binary / "az", """#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["TEST_ROOT"])
args = sys.argv[1:]
if os.environ.get("TEST_AZ_FAIL") == "1":
    sys.exit("Synthetic Azure command failure")
with (root / "az-calls.jsonl").open("a") as file:
    file.write(json.dumps(args) + "\\n")
if args[:2] == ["account", "show"]:
    print(json.dumps({"id": os.environ["TEST_SUBSCRIPTION"], "tenantId": "22222222-2222-2222-2222-222222222222"}))
elif args[:3] == ["deployment", "group", "list"]:
    print('[]' if os.environ.get("TEST_NO_FOUNDATION") == "1" else '["foundation"]')
elif args[:3] == ["deployment", "group", "show"]:
    if args[args.index("-n") + 1] == "delivery":
        print('{"testApiClientId":{"value":"test-api-client"},"testWorkerClientId":{"value":"test-worker-client"}}')
    else:
        print((root / "outputs.json").read_text())
elif args[:2] == ["acr", "login"]:
    print(json.dumps({"loginServer": os.environ.get("TEST_REGISTRY", "testregistry.azurecr.io"), "accessToken": "synthetic-test-token"}))
elif args[:3] in (["deployment", "group", "what-if"], ["deployment", "group", "create"]):
    pass
else:
    sys.exit("Unexpected Azure command: " + repr(args))
""")
        self.mock(binary / "podman", """#!/usr/bin/env python3
import json, os, pathlib, sys
path = pathlib.Path(os.environ["TEST_ROOT"]) / "podman-call.json"
path.write_text(json.dumps({"args":sys.argv[1:], "stdin":sys.stdin.read()}))
print("Login Succeeded!")
""")
        self.mock(binary / "kubectl", """#!/usr/bin/env python3
import pathlib, re, sys
if sys.argv[1] != "kustomize":
    sys.exit("Only offline kustomize is allowed in these tests.")
root = pathlib.Path(sys.argv[2])
if not (root / "kustomization.yaml").is_file():
    sys.exit("Missing Kustomization")
for path in root.rglob("*.yaml"):
    if re.search(r"__[A-Z_]+__", path.read_text()):
        sys.exit("Unresolved placeholder: " + str(path))
print("offline render")
""")

    @staticmethod
    def mock(path, text):
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, script, *args, input=None, success=True):
        result = subprocess.run(["bash", str(self.root / script), *args],
                                cwd=self.root, env=self.env, input=input,
                                text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_loading_context_and_generated_names(self):
        result = subprocess.run(
            ["bash", "-c", 'source scripts/use-lab.sh; lab_value KeyVaultName; output_value apiClientId'],
            cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["generated-vault", "api-client"])
        self.env["TEST_SUBSCRIPTION"] = ADMIN
        self.run_script("scripts/use-lab.sh", success=False)

    def test_invalid_namespace_fails_before_azure(self):
        self.settings["Namespace"] = "other"
        (self.root / "local.settings.json").write_text(json.dumps(self.settings))
        self.run_script("scripts/use-lab.sh", success=False)
        self.assertFalse((self.root / "az-calls.jsonl").exists())

    def test_context_without_foundation_and_native_failures(self):
        self.env["TEST_NO_FOUNDATION"] = "1"
        result = subprocess.run(
            ["bash", "-c", 'source scripts/use-lab.sh; lab_value ClusterName; printf "%s\\n" "$Outputs"'],
            cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["testlab-aks", "{}"])
        self.env["TEST_AZ_FAIL"] = "1"
        result = self.run_script("scripts/connect-acr-podman.sh", "--registry-name", "testregistry",
                                 "--registry-server", "testregistry.azurecr.io", success=False)
        self.assertIn("Synthetic Azure command failure", result.stderr)
        self.assertFalse((self.root / "podman-call.json").exists())

    def test_acr_token_uses_stdin_and_checks_hostname(self):
        args = ("--registry-name", "testregistry", "--registry-server", "testregistry.azurecr.io")
        result = self.run_script("scripts/connect-acr-podman.sh", *args)
        call = json.loads((self.root / "podman-call.json").read_text())
        self.assertEqual(call["stdin"], "synthetic-test-token")
        self.assertIn("--password-stdin", call["args"])
        self.assertNotIn("synthetic-test-token", result.stdout + result.stderr)
        (self.root / "podman-call.json").unlink()
        self.env["TEST_REGISTRY"] = "wrong.azurecr.io"
        self.run_script("scripts/connect-acr-podman.sh", *args, success=False)
        self.assertFalse((self.root / "podman-call.json").exists())

    def test_argument_errors_do_not_call_azure(self):
        for args in (("--unknown", "value"), ("--registry-name",), ("--name", "unexpected")):
            self.run_script("scripts/connect-acr-podman.sh", *args, success=False)
        self.assertFalse((self.root / "az-calls.jsonl").exists())

    def test_render_and_one_shot_gitops_adoption(self):
        self.run_script("scripts/render-manifests.sh")
        source = self.root / "rendered/base"
        secret_provider = (self.root / "k8s/identity/secret-provider.yaml").read_text()
        for token, value in {"__API_CLIENT_ID__": "api-client", "__KEY_VAULT__": "generated-vault",
                             "__TENANT_ID__": TENANT}.items():
            secret_provider = secret_provider.replace(token, value)
        (source / "secret-provider.yaml").write_text(secret_provider)
        shutil.copy(self.root / "k8s/identity/mount-patch.yaml", source / "identity-patch.yaml")
        shutil.copy(self.root / "k8s/network/policies.yaml", source / "policies.yaml")
        kustomization = source / "kustomization.yaml"
        kustomization.write_text(kustomization.read_text().replace(
            "resources:\n", "resources:\n  - secret-provider.yaml\n  - policies.yaml\n")
            + "patches:\n  - path: identity-patch.yaml\n")
        self.run_script("ops/initialize-gitops.sh")
        primary = self.root / "gitops/clusters/primary/apps/orders"
        test = self.root / "gitops/clusters/primary/apps/orders-test"
        self.assertTrue((primary / "secret-provider.yaml").exists())
        self.assertFalse((test / "secret-provider.yaml").exists())
        self.assertNotIn("identity-patch.yaml", (test / "kustomization.yaml").read_text())
        self.assertNotIn("- namespace.yaml", (primary / "kustomization.yaml").read_text())
        self.assertIn("test-api-client", (test / "serviceaccounts.yaml").read_text())
        self.assertIn("QUEUE_NAME=orders-test", (test / "kustomization.yaml").read_text())
        if shutil.which("kubectl"):
            for directory in (primary, test):
                result = subprocess.run([shutil.which("kubectl"), "kustomize", str(directory)],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("__", result.stdout)
        self.run_script("ops/initialize-gitops.sh", success=False)

    def test_image_and_resource_edits(self):
        self.run_script("scripts/render-manifests.sh")
        target = self.root / "gitops/clusters/primary/apps/orders"
        shutil.copytree(self.root / "rendered/base", target)
        digest = "sha256:" + "a" * 64
        self.run_script("ops/set-gitops-image.sh", "--namespace", "orders", "--digest", digest)
        self.assertIn("digest: " + digest, (target / "kustomization.yaml").read_text())
        resource = self.root / "extra.yaml"
        resource.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: extra\n")
        self.run_script("ops/add-gitops-file.sh", "--source", str(resource))
        self.assertIn("- extra.yaml", (target / "kustomization.yaml").read_text())
        self.run_script("ops/remove-gitops-file.sh", "--name", "extra.yaml")
        self.assertFalse((target / "extra.yaml").exists())
        self.run_script("ops/remove-gitops-file.sh", "--name", "../bad.yaml", success=False)

    def test_foundation_is_what_if_by_default(self):
        keys = self.root / "rendered/keys"
        keys.mkdir(parents=True)
        (keys / "aks.pub").write_text("ssh-rsa synthetic-public-key")
        self.run_script("scripts/deploy-foundation.sh")
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertTrue(any(call[:3] == ["deployment", "group", "what-if"] for call in calls))
        self.assertFalse(any(call[:3] == ["deployment", "group", "create"] for call in calls))
        self.run_script("scripts/deploy-foundation.sh", "--apply", success=False)
        self.run_script("scripts/deploy-foundation.sh", "--apply", "--confirm", input="wrong-group\n", success=False)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required")
    def test_certificate_contains_hostname_and_private_key_permissions(self):
        self.run_script("scripts/new-lab-certificate.sh", "--hostname", "orders.example.test")
        certificate = self.root / "rendered/certs/tls.crt"
        result = subprocess.run(["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"],
                                text=True, capture_output=True, check=True)
        self.assertIn("DNS:orders.example.test", result.stdout)
        self.assertEqual((self.root / "rendered/certs/tls.key").stat().st_mode & 0o777, 0o600)

    def test_bounded_load_preserves_report_shape_and_http_failures(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(202)
                self.end_headers()

            def do_GET(self):
                self.send_response(503)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        uri = f"http://127.0.0.1:{server.server_port}"
        for operation, expected in (("PostOrders", 202), ("Health", 503)):
            self.run_script("ops/invoke-order-load.sh", "--base-uri", uri, "--count", "3",
                            "--delay-milliseconds", "0", "--operation", operation)
            report = json.loads((self.root / ".artifacts/order-load.json").read_text())
            self.assertEqual(report["requested"], 3)
            self.assertEqual(report["sent"], 3)
            self.assertEqual({row["status"] for row in report["requests"]}, {expected})
            self.assertEqual(report["availabilityPercent"], 100 if expected == 202 else 0)
        self.run_script("ops/invoke-order-load.sh", "--base-uri", uri, "--count", "2001", success=False)


if __name__ == "__main__":
    unittest.main()
