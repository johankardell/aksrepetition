import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse

REPO = Path(__file__).resolve().parents[1]
REAL_KUBECTL = shutil.which("kubectl")
DISPATCH = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
call = {"tool": tool, "args": args}
if tool == "psql":
    assert os.environ["PGHOST"] == os.environ.get("MOCK_PGHOST", "db.example")
    assert os.environ["PGUSER"] == os.environ.get("MOCK_PGUSER", "admin")
    assert os.environ["PGSSLMODE"] == "verify-full"
    assert os.environ["PGSSLROOTCERT"] == "system"
    assert os.environ["PGPASSWORD"] == "mock-token"
    call["host"] = os.environ["PGHOST"]
    call["user"] = os.environ["PGUSER"]
    call["sql"] = sys.stdin.read()
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps(call) + "\n")
command = " ".join([tool] + args)
if os.environ.get("MOCK_FAIL") and os.environ["MOCK_FAIL"] in command:
    sys.exit(23)
if tool == "kubectl":
    assert args[0] == "kustomize", "No live kubectl operations allowed"
    if os.environ.get("REAL_KUBECTL"):
        result = subprocess.run([os.environ["REAL_KUBECTL"]] + args, timeout=10)
        if result.returncode: sys.exit(result.returncode)
    else:
        root = pathlib.Path(args[1])
        assert (root / "kustomization.yaml").is_file(), "Missing Kustomization"
        for path in sorted(root.rglob("*.yaml")):
            if path.name not in ("kustomization.yaml", "namespace.yaml"):
                print(path.read_text())
    print(os.environ.get("MOCK_RENDER_EXTRA", ""))
elif tool == "git":
    if args[:2] == ["branch", "--show-current"]: print(os.environ.get("MOCK_BRANCH", "main"))
    elif args[:3] == ["diff", "--cached", "--name-only"]: print(os.environ.get("MOCK_STAGED", "advanced/example.yaml"))
    elif args[:2] == ["rev-parse", "HEAD"]: print("current")
    elif args[:2] == ["rev-parse", "origin/main"]: print(os.environ.get("MOCK_ORIGIN", "current"))
    elif args[0] not in ("fetch", "switch", "commit", "push", "pull"):
        sys.exit("Unexpected Git command: " + repr(args))
elif tool == "gh":
    if args[:2] == ["pr", "create"]: print("https://github.example/repo/pull/1")
    elif args[:2] == ["pr", "view"]: print(os.environ.get("MOCK_PR_STATE", "MERGED"))
    else: sys.exit("Unexpected GitHub command: " + repr(args))
elif tool == "az":
    if args[:3] == ["account", "get-access-token", "--resource"]: print(os.environ.get("MOCK_TOKEN", "mock-token"))
    elif args[:4] == ["network", "private-dns", "zone", "show"]: print("/zones/blob")
    elif args[:5] == ["network", "private-dns", "link", "vnet", "list"]:
        print(os.environ.get("MOCK_LINKS", "[]"))
    elif args[:3] == ["network", "private-endpoint", "show"]:
        print('{"id":"/endpoints/backup","state":"Approved"}')
    elif args[:3] == ["storage", "account", "show"]: print("/storage/backup")
    elif args[:2] == ["k8s-extension", "show"]: print("extension-principal")
    elif args[:3] == ["dataprotection", "backup-policy", "get-default-policy-template"]:
        print('{"properties":{"policyRules":[]}}')
    elif args[:3] == ["dataprotection", "backup-policy", "show"]:
        print(os.environ.get("MOCK_POLICY", '{"properties":{"policyRules":[{"objectType":"AzureBackupRule","name":"daily"}]}}'))
    elif args[:3] == ["dataprotection", "backup-instance", "initialize-backupconfig"]:
        print('{"included_namespaces":[],"include_cluster_scope_resources":false,"snapshot_volumes":false,"preserved":{"deep":[1,2]}}')
    elif args[:3] == ["dataprotection", "backup-instance", "initialize-restoreconfig"]:
        print('{"included_namespaces":[],"namespace_mappings":{},"preserved":{"deep":[1,2]}}')
    elif args[:3] == ["dataprotection", "backup-instance", "initialize"]:
        print('{"id":"/instances/storage-lab","name":"storage-lab","properties":{"preserved":true}}')
    elif args[:3] == ["dataprotection", "backup-instance", "create"]:
        print('{"id":"/instances/storage-lab","name":"storage-lab"}')
    elif args[:4] == ["dataprotection", "backup-instance", "restore", "initialize-for-item-recovery"]:
        print('{"restore":"request","nested":{"preserved":true}}')
    elif any(tuple(args[:len(prefix)]) == prefix for prefix in (
        ("provider", "register"), ("group", "create"),
        ("storage", "account", "create"), ("storage", "container-rm", "create"),
        ("network", "private-dns", "zone", "create"),
        ("network", "private-dns", "link", "vnet", "create"),
        ("network", "private-endpoint", "create"),
        ("network", "private-endpoint", "dns-zone-group", "create"),
        ("dataprotection", "backup-vault", "create"),
        ("dataprotection", "backup-policy", "create"),
        ("k8s-extension", "create"), ("role", "assignment", "create"),
        ("aks", "trustedaccess", "rolebinding", "create"),
        ("dataprotection", "backup-instance", "update-msi-permissions"),
        ("dataprotection", "backup-instance", "validate-for-backup"),
        ("dataprotection", "backup-instance", "validate-for-restore"),
        ("dataprotection", "backup-instance", "adhoc-backup"),
        ("dataprotection", "backup-instance", "restore", "trigger"),
    )):
        pass
    else: sys.exit("Unexpected Azure command: " + repr(args))
elif tool not in ("psql", "gitops-file"):
    sys.exit("Unexpected mocked tool: " + tool)
'''


class Handler(http.server.BaseHTTPRequestHandler):
    ready_status = 200
    actual_item = "widget"
    seen = []
    body_delay = 0
    orders_status = 200
    actual_id = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen.append(self.path)
        if self.path == "/readyz":
            self.send_response(type(self).ready_status)
            self.end_headers()
            time.sleep(type(self).body_delay)
            self.wfile.write(b"ready")
        elif self.path.startswith("/orders/"):
            order_id = urllib.parse.unquote(self.path[len("/orders/"):])
            self.send_response(type(self).orders_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": type(self).actual_id or order_id,
                                        "item": type(self).actual_item}).encode())
        else:
            self.send_response(404)
            self.end_headers()


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Bash and jq required")
class AdvancedHelpersTests(unittest.TestCase):
    shell = "bash"

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join)
        cls.addClassCleanup(cls.server.shutdown)
        cls.uri = f"http://127.0.0.1:{cls.server.server_port}"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="advanced offline ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("advanced", "scripts", "ops", "bin"):
            (self.root / directory).mkdir()
        for script in (REPO / "advanced").glob("*.sh"):
            shutil.copy2(script, self.root / "advanced" / script.name)
        shutil.copy2(REPO / "scripts/lib.sh", self.root / "scripts/lib.sh")
        shutil.copytree(REPO / "k8s/base", self.root / "k8s/base")
        (self.root / "scripts/use-lab.sh").write_text('''#!/usr/bin/env bash
Root=$MOCK_ROOT
Lab='{"ResourceGroup":"lab-rg","Location":"eastus","Prefix":"abcdefghijkl","SubscriptionId":"12345678-1234-1234-1234-123456789abc","ClusterName":"abcdefghijkl-aks"}'
Outputs='{"endpointsSubnetId":{"value":"/network/endpoints"},"vnetId":{"value":"/network/vnet"},"clusterId":{"value":"/clusters/aks"}}'
lab_value() { jq -er --arg key "$1" '.[$key]' <<< "$Lab"; }
output_value() { jq -er --arg key "$1" '.[$key].value' <<< "$Outputs"; }
''')
        (self.root / "bin/dispatch").write_text(DISPATCH)
        (self.root / "bin/dispatch").chmod(0o755)
        for name in ("az", "git", "gh", "psql", "kubectl", "gitops-file"):
            (self.root / "bin" / name).symlink_to("dispatch")
        (self.root / "ops/add-gitops-file.sh").write_text('#!/usr/bin/env bash\nset -euo pipefail\ngitops-file "$@"\n')
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("MOCK_") and key.lower() not in
                    ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
        self.env.update(PATH=str(self.root / "bin") + ":" + os.environ.get("PATH", os.defpath),
                        MOCK_ROOT=str(self.root), MOCK_LOG=str(self.root / "calls.jsonl"),
                        REAL_KUBECTL=REAL_KUBECTL or "",
                        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
                        SSL_CERT_FILE="", CURL_CA_BUNDLE="")
        Handler.seen = []
        Handler.ready_status = 200
        Handler.actual_item = "widget"
        Handler.body_delay = 0
        Handler.orders_status = 200
        Handler.actual_id = None

    def run_script(self, script, args=(), success=True, input="", **env):
        result = subprocess.run([self.shell, str(self.root / "advanced" / (script + ".sh")), *args],
                                cwd=self.root, env=dict(self.env, **env), input=input,
                                text=True, capture_output=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def reset_calls(self):
        (self.root / "calls.jsonl").unlink(missing_ok=True)

    def regional_args(self):
        return ["--output-directory", "regional output", "--registry-server", "example.azurecr.io",
                "--image-digest", "sha256:" + "a" * 64, "--service-bus-namespace", "servicebus",
                "--api-client-id", "12345678-1234-1234-1234-123456789abc",
                "--worker-client-id", "12345678-1234-1234-1234-123456789abd",
                "--postgres-host", "db.example", "--postgres-private-ip", "10.0.0.9"]

    def test_required_unknown_missing_arguments(self):
        for path in (self.root / "advanced").glob("*.sh"):
            for args in ([], ["--bogus", "value"], ["--host-name"]):
                with self.subTest(script=path.stem, args=args):
                    self.run_script(path.stem, args, success=False)
        self.assertEqual(self.calls(), [])

    @unittest.skipUnless(REAL_KUBECTL, "kubectl required for real offline rendering")
    def test_regional_overlay_real_kustomize_and_replica_edits(self):
        self.run_script("new-regional-overlay", self.regional_args())
        rendered = subprocess.check_output([REAL_KUBECTL, "kustomize", str(self.root / "regional output")],
                                           text=True, timeout=10)
        self.assertIn("sha256:" + "a" * 64, rendered)
        self.assertEqual(rendered.count("replicas: 0"), 2)
        self.assertNotIn("kind: Namespace", rendered)
        self.assertNotIn("__", rendered)
        self.assertIn("runAsUser: 10001", rendered)
        self.assertIn("readOnlyRootFilesystem: true", rendered)
        self.run_script("set-regional-replicas", ["--directory", "regional output", "--api", "010", "--worker", "+0"])
        text = (self.root / "regional output/kustomization.yaml").read_text()
        self.assertIn("name: order-api\n    count: 10", text)
        self.assertIn("name: order-worker\n    count: 0", text)
        path = self.root / "regional output/kustomization.yaml"
        path.write_text(text.replace("  - name: order-worker\n    count: 0", ""))
        before = path.read_text()
        self.run_script("set-regional-replicas", ["--directory", "regional output", "--api", "1", "--worker", "1"], success=False)
        self.assertEqual(path.read_text(), before)

    def test_regional_scaler_and_placeholder_fences(self):
        for kind in ("HorizontalPodAutoscaler", "ScaledObject", "TriggerAuthentication", "ClusterTriggerAuthentication"):
            with self.subTest(kind=kind):
                result = self.run_script("new-regional-overlay", self.regional_args(), success=False,
                                         MOCK_RENDER_EXTRA=f"kind: {kind}")
                self.assertIn("must not inherit autoscalers", result.stderr)
        for token in ("__SCALER_CLIENT_ID__", "__UNRESOLVED_TOKEN__"):
            result = self.run_script("new-regional-overlay", self.regional_args(), success=False, MOCK_RENDER_EXTRA=token)
            self.assertTrue("autoscalers" in result.stderr or "unresolved" in result.stderr)
        (self.root / "k8s/base/api.yaml").write_text("__UNKNOWN_TOKEN__")
        result = self.run_script("new-regional-overlay", self.regional_args(), success=False)
        self.assertIn("Unresolved placeholder", result.stderr)

    def test_replica_defaults_and_exactly_one_entry(self):
        directory = self.root / "advanced/regions/secondary"
        directory.mkdir(parents=True)
        for name in ("api.yaml", "worker.yaml"):
            shutil.copy2(self.root / "k8s/base" / name, directory / name)
        path = directory / "kustomization.yaml"
        text = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - api.yaml
  - worker.yaml
replicas:
  - name: order-api
    count: 0
  - name: order-worker
    count: 0
"""
        path.write_text(text)
        self.run_script("set-regional-replicas", ["--api", "10", "--worker", "3"])
        updated = path.read_text()
        self.assertIn("name: order-api\n    count: 10", updated)
        self.assertIn("name: order-worker\n    count: 3", updated)
        for malformed in (text.replace("  - name: order-worker\n    count: 0", ""),
                          text + "  - name: order-api\n    count: 0\n"):
            path.write_text(malformed)
            self.reset_calls()
            self.run_script("set-regional-replicas", ["--api", "1", "--worker", "1"], success=False)
            self.assertEqual(path.read_text(), malformed)
            self.assertEqual(self.calls(), [])

    def test_ranges_and_yaml_input_constraints(self):
        for value in ("-1", "11", "1.5", "18446744073709551616"):
            self.run_script("new-regional-overlay", self.regional_args() + ["--api-replicas", value], success=False)
            self.run_script("set-regional-replicas", ["--api", value, "--worker", "0"], success=False)
        for value in ("0", "7201", "abc"):
            self.run_script("measure-orders", ["--base-uri", self.uri, "--seconds", value], success=False)
        for value in ("0", "3601", "abc"):
            self.run_script("test-order-ledger", ["--base-uri", self.uri, "--timeout-seconds", value], success=False)
        self.run_script("new-regional-overlay", self.regional_args() + ["--api-role", "bad\nrole"], success=False)
        self.run_script("new-regional-overlay", self.regional_args() + ["--image-digest", "latest"], success=False)
        self.assertEqual(self.calls(), [])

    def test_origin_manifest_escaped_nginx_variables_real_render(self):
        self.run_script("new-origin-manifest", ["--origin-host", "origin.example", "--output-directory", "origin output",
                                               "--image", "registry/nginx@sha256:" + "b" * 64])
        path = self.root / "origin output"
        text = (path / "origin.yaml").read_text()
        self.assertIn("proxy_set_header Host $host;", text)
        self.assertIn("$proxy_add_x_forwarded_for;", text)
        self.assertIn("runAsUser: 101", text)
        self.assertIn('service.beta.kubernetes.io/azure-pls-visibility: "*"', text)
        (path / "kustomization.yaml").write_text("resources:\n  - origin.yaml\n")
        if REAL_KUBECTL:
            result = subprocess.run([REAL_KUBECTL, "kustomize", str(path)], capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.run_script("new-origin-manifest", ["--origin-host", "bad;host", "--output-directory", "bad",
                                               "--image", "nginx@sha256:" + "b" * 64], success=False)
        self.run_script("new-origin-manifest", ["--origin-host", "origin.example", "--output-directory", "bad",
                                               "--image", "nginx\n@sha256:" + "b" * 64], success=False)

    def test_primary_database_interfaces_and_native_failure(self):
        self.run_script("set-primary-database", ["--host-name", "db.example", "--private-ip", "10.0.0.8"])
        calls = self.calls()
        self.assertEqual([c["args"] for c in calls], [
            ["--source", "./.artifacts/advanced/database-patch.yaml", "--kind", "Patch", "--namespace", "orders"],
            ["--source", "./.artifacts/advanced/database-egress.yaml", "--kind", "Resource", "--namespace", "orders"]])
        self.assertIn('value: "5432"', (self.root / ".artifacts/advanced/database-patch.yaml").read_text())
        self.assertIn("10.0.0.8/32", (self.root / ".artifacts/advanced/database-egress.yaml").read_text())
        self.reset_calls()
        self.run_script("set-primary-database", ["--host-name", "db.example", "--private-ip", "10.0.0.8"],
                        success=False, MOCK_FAIL="gitops-file")
        self.assertEqual(len(self.calls()), 1)

    def test_private_endpoint_existing_link_and_case_insensitive_id(self):
        args = ["--resource-group", "rg", "--location", "eastus", "--name", "backup",
                "--resource-id", "/storage/backup", "--group-id", "blob", "--subnet-id", "/subnet",
                "--vnet-id", "/VNET", "--zone-name", "privatelink.blob.core.windows.net"]
        self.run_script("new-private-endpoint", args)
        self.assertTrue(any(c["args"][:5] == ["network", "private-dns", "link", "vnet", "create"] for c in self.calls()))
        self.reset_calls()
        self.run_script("new-private-endpoint", args, MOCK_LINKS='[{"virtualNetwork":{"id":"/vnet"}}]')
        self.assertFalse(any(c["args"][:5] == ["network", "private-dns", "link", "vnet", "create"] for c in self.calls()))
        self.reset_calls()
        self.run_script("new-private-endpoint", args, success=False, MOCK_LINKS="invalid JSON")
        self.assertFalse(any(c["args"][:5] == ["network", "private-dns", "link", "vnet", "create"] for c in self.calls()))
        self.assertFalse(any(c["args"][:3] == ["network", "private-endpoint", "create"] for c in self.calls()))

    def test_database_token_and_sql_permissions(self):
        args = ["--host-name", "db.example", "--admin-login", "admin",
                "--api-principal-id", "12345678-1234-1234-1234-123456789abc",
                "--worker-principal-id", "12345678-1234-1234-1234-123456789abd"]
        result = self.run_script("initialize-orders-database", args)
        self.assertNotIn("mock-token", result.stdout + result.stderr)
        psql = [c for c in self.calls() if c["tool"] == "psql"]
        self.assertEqual(len(psql), 2)
        self.assertIn("pgaadauth_create_principal_with_oid('orders_api'", psql[0]["sql"])
        self.assertIn('GRANT INSERT ON public.processed_orders TO "orders_worker";', psql[1]["sql"])
        self.assertNotIn('GRANT INSERT ON public.processed_orders TO "orders_api"', psql[1]["sql"])
        self.assertIn("order_id text PRIMARY KEY", psql[1]["sql"])
        self.assertIn("REVOKE CREATE ON SCHEMA public FROM PUBLIC;", psql[1]["sql"])
        self.assertEqual(psql[0]["args"], ["--dbname", "postgres", "--set", "ON_ERROR_STOP=1"])
        self.assertEqual(psql[1]["args"], ["--dbname", "ordersdb", "--set", "ON_ERROR_STOP=1"])
        self.reset_calls()
        self.run_script("initialize-orders-database", args, success=False, MOCK_FAIL="psql --dbname postgres")
        self.assertEqual(len([c for c in self.calls() if c["tool"] == "psql"]), 1)
        self.reset_calls()
        for option, value in (("--api-role", "injection';"), ("--worker-role", "worker role"),
                              ("--api-principal-id", "bad-id"),
                              ("--worker-principal-id", "'" + "a" * 35)):
            self.run_script("initialize-orders-database", args + [option, value], success=False)
        self.assertEqual(self.calls(), [])
        self.run_script("initialize-orders-database", args, success=False, MOCK_TOKEN="")
        self.assertFalse(any(call["tool"] == "psql" for call in self.calls()))

    def test_database_connection_parameters_remain_literal(self):
        host = "db host;$(printf unexpected)"
        user = "admin user';--"
        args = ["--host-name", host, "--admin-login", user,
                "--api-principal-id", "12345678-1234-1234-1234-123456789abc",
                "--worker-principal-id", "12345678-1234-1234-1234-123456789abd",
                "--api-role", "api_dr", "--worker-role", "worker_dr"]
        self.run_script("initialize-orders-database", args, MOCK_PGHOST=host, MOCK_PGUSER=user)
        calls = [call for call in self.calls() if call["tool"] == "psql"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call["host"], host)
            self.assertEqual(call["user"], user)
            self.assertNotIn(host, call["sql"])
            self.assertNotIn(user, call["sql"])
        self.assertIn("pgaadauth_create_principal_with_oid('api_dr'", calls[0]["sql"])
        self.assertIn('GRANT INSERT ON public.processed_orders TO "worker_dr";', calls[1]["sql"])

    def test_backup_configure_restore_json_and_submission_gates(self):
        self.run_script("invoke-aks-backup", ["--operation", "Configure"])
        directory = self.root / ".artifacts/advanced"
        config = json.loads((directory / "backup-config.json").read_text())
        self.assertEqual(config, {"included_namespaces": ["storage-lab"], "include_cluster_scope_resources": True,
                                  "snapshot_volumes": True, "preserved": {"deep": [1, 2]}})
        calls = self.calls()
        self.assertTrue(any("abcdefghijbackup12345678" in c["args"] for c in calls))
        validation = next(i for i, c in enumerate(calls) if c["args"][:3] == ["dataprotection", "backup-instance", "validate-for-backup"])
        creation = next(i for i, c in enumerate(calls) if c["args"][:3] == ["dataprotection", "backup-instance", "create"])
        self.assertLess(validation, creation)
        self.reset_calls()
        self.run_script("invoke-aks-backup", ["--operation", "BACKUP"])
        self.assertEqual(self.calls()[-1]["args"], ["dataprotection", "backup-instance", "adhoc-backup",
                                                  "--rule-name", "daily", "--ids", "/instances/storage-lab"])
        self.reset_calls()
        self.run_script("invoke-aks-backup", ["--operation", "Restore"], success=False)
        self.assertEqual(self.calls(), [])
        self.run_script("invoke-aks-backup", ["--operation", "restore", "--recovery-point-id", "verified-point"])
        restore = json.loads((directory / "restore-config.json").read_text())
        self.assertEqual(restore["namespace_mappings"], {"storage-lab": "storage-restored"})
        self.assertEqual(restore["conflict_policy"], "Skip")
        self.assertEqual(restore["persistent_volume_restore_mode"], "RestoreWithVolumeData")
        self.assertEqual(restore["preserved"], {"deep": [1, 2]})
        self.assertEqual(json.loads((directory / "restore-request.json").read_text()),
                         {"restore": "request", "nested": {"preserved": True}})
        calls = self.calls()
        self.assertEqual(calls[-2]["args"][:3], ["dataprotection", "backup-instance", "validate-for-restore"])
        self.assertEqual(calls[-1]["args"][:4], ["dataprotection", "backup-instance", "restore", "trigger"])

    def test_backup_validation_failures_do_not_trigger(self):
        self.run_script("invoke-aks-backup", ["--operation", "Configure"], success=False,
                        MOCK_FAIL="dataprotection backup-instance validate-for-backup")
        self.assertFalse(any(c["args"][:3] == ["dataprotection", "backup-instance", "create"] for c in self.calls()))
        directory = self.root / ".artifacts/advanced"
        (directory / "backup-created.json").write_text('{"id":"/instances/storage-lab","name":"storage-lab"}')
        self.reset_calls()
        self.run_script("invoke-aks-backup", ["--operation", "Backup"], success=False,
                        MOCK_POLICY='{"properties":{"policyRules":[]}}')
        self.assertFalse(any("adhoc-backup" in c["args"] for c in self.calls()))
        self.reset_calls()
        self.run_script("invoke-aks-backup", ["--operation", "Restore", "--recovery-point-id", "point"],
                        success=False, MOCK_FAIL="dataprotection backup-instance validate-for-restore")
        self.assertFalse(any("trigger" in c["args"] for c in self.calls()))

    def test_publishing_review_gate(self):
        self.run_script("publish-reviewed-change", ["--message", "reviewed change"], input="\n")
        calls = self.calls()
        switch = [c["args"] for c in calls if c["tool"] == "git" and c["args"][0] == "switch"]
        self.assertRegex(switch[0][2], r"^lab-[a-f0-9]{12}$")
        self.assertEqual(switch[-1], ["switch", "main"])
        self.assertEqual(calls[-2]["args"], ["pull", "--ff-only"])
        self.assertFalse(any("merge" in c["args"] for c in calls))
        for input_text, state in (("stop\n", "OPEN"), ("", "OPEN"), ("\nstop\n", "OPEN"), ("\n", "CLOSED")):
            self.reset_calls()
            self.run_script("publish-reviewed-change", ["--message", "change"], input=input_text,
                            success=False, MOCK_PR_STATE=state)
            self.assertFalse(any(c["args"] == ["switch", "main"] for c in self.calls()))

    def test_publishing_main_staging_and_freshness_preconditions(self):
        for env in ({"MOCK_BRANCH": "topic"}, {"MOCK_STAGED": ""}, {"MOCK_ORIGIN": "stale"}):
            self.reset_calls()
            self.run_script("publish-reviewed-change", ["--message", "change"], success=False, **env)
            self.assertFalse(any(c["args"][0] == "switch" for c in self.calls()))
        self.reset_calls()
        self.run_script("publish-reviewed-change", ["--message", "change"], success=False, MOCK_FAIL="git push")
        self.assertFalse(any(c["tool"] == "gh" for c in self.calls()))

    def test_http_measurement_shapes_and_http_errors(self):
        Handler.body_delay = 0.12
        result = self.run_script("measure-orders", ["--base-uri", self.uri + "/", "--seconds", "1",
                                                   "--output-path", "traffic.json"])
        summary = json.loads(result.stdout)
        self.assertEqual(summary["Samples"], 1)
        self.assertEqual(summary["Successes"], 1)
        self.assertEqual(summary["AvailabilityPercent"], 100)
        observations = json.loads((self.root / "traffic.json").read_text())
        self.assertEqual(set(observations[0]), {"utc", "status", "milliseconds", "error"})
        self.assertEqual(observations[0]["status"], 200)
        self.assertGreaterEqual(observations[0]["milliseconds"], 100)
        Handler.ready_status = 503
        result = self.run_script("measure-orders", ["--base-uri", self.uri, "--seconds", "1", "--output-path", "error.json"])
        self.assertEqual(json.loads(result.stdout)["AvailabilityPercent"], 0)
        sample = json.loads((self.root / "error.json").read_text())[0]
        self.assertEqual(sample["status"], 503)
        self.assertEqual(sample["error"], "")

    def test_order_ledger_shape_escaping_case_mismatch_empty(self):
        ledger = self.root / "ledger.json"
        order = {"id": "ABC /?#", "item": "widget"}
        ledger.write_text(json.dumps([order]))
        args = ["--base-uri", self.uri, "--ledger-path", "ledger.json", "--timeout-seconds", "1", "--report-path", "report.json"]
        self.run_script("test-order-ledger", args)
        self.assertEqual(Handler.seen, ["/orders/ABC%20%2F%3F%23"])
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(set(report), {"utc", "expected", "unverified", "results"})
        self.assertEqual(report["results"], [dict(order, verified=True, error="")])
        ledger.write_text(json.dumps([order, {"id": "second", "item": "widget"}]))
        self.run_script("test-order-ledger", args)
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(report["expected"], 2)
        self.assertEqual(report["unverified"], 0)
        self.assertEqual(len(report["results"]), 2)
        ledger.write_text(json.dumps(order))
        self.run_script("test-order-ledger", args)
        Handler.actual_item = "WIDGET"
        self.run_script("test-order-ledger", args, success=False)
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(report["unverified"], 1)
        self.assertEqual(report["results"][0]["error"], "ID/item mismatch.")
        Handler.actual_item = "widget"
        Handler.actual_id = "abc /?#"
        self.run_script("test-order-ledger", args, success=False)
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(report["unverified"], 1)
        Handler.actual_id = None
        Handler.orders_status = 404
        self.run_script("test-order-ledger", args, success=False)
        report = json.loads((self.root / "report.json").read_text())
        self.assertIn("404", report["results"][0]["error"])
        ledger.write_text("[]")
        Handler.seen = []
        self.run_script("test-order-ledger", args, success=False)
        self.assertEqual(Handler.seen, [])
        ledger.write_text('[{"id":"missing-item"}]')
        self.run_script("test-order-ledger", args, success=False)
        self.assertEqual(Handler.seen, [])
        for script in ("test-order-ledger", "measure-orders"):
            self.run_script(script, ["--base-uri", "not-a-uri"], success=False)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required for local HTTPS fixture")
    def test_https_approved_bundles_and_untrusted_certificate(self):
        cert, key = self.root / "ca.pem", self.root / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        uri = f"https://127.0.0.1:{server.server_port}"
        (self.root / "ledger.json").write_text('[{"id":"tls-order","item":"widget"}]')
        measurement_args = ["--base-uri", uri, "--seconds", "1", "--output-path", "tls-traffic.json"]
        ledger_args = ["--base-uri", uri, "--ledger-path", "ledger.json",
                       "--timeout-seconds", "1", "--report-path", "tls-report.json"]
        self.run_script("measure-orders", measurement_args, SSL_CERT_FILE="", CURL_CA_BUNDLE="")
        sample = json.loads((self.root / "tls-traffic.json").read_text())[0]
        self.assertEqual(sample["status"], 0)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", sample["error"])
        self.run_script("test-order-ledger", ledger_args, success=False, SSL_CERT_FILE="", CURL_CA_BUNDLE="")
        report = json.loads((self.root / "tls-report.json").read_text())
        self.assertEqual(report["unverified"], 1)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", report["results"][0]["error"])
        for env in (
            {"SSL_CERT_FILE": str(cert), "CURL_CA_BUNDLE": ""},
            {"SSL_CERT_FILE": "", "CURL_CA_BUNDLE": str(cert)},
            {"SSL_CERT_FILE": str(cert), "CURL_CA_BUNDLE": str(self.root / "missing.pem")},
        ):
            result = self.run_script("measure-orders", measurement_args, **env)
            self.assertEqual(json.loads(result.stdout)["AvailabilityPercent"], 100)
            self.run_script("test-order-ledger", ledger_args, **env)
            report = json.loads((self.root / "tls-report.json").read_text())
            self.assertEqual(report["unverified"], 0)


@unittest.skipUnless(shutil.which("zsh"), "Zsh is not installed")
class ZshAdvancedHelpersTests(AdvancedHelpersTests):
    shell = "zsh"


if __name__ == "__main__":
    unittest.main()
