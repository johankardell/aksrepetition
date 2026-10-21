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
BASTION_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/test-rg/providers/Microsoft.Network/bastionHosts/testlab-bastion"


class BashHelpersTests(unittest.TestCase):
    shell = "bash"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shell helpers ")
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
import json, os, pathlib, socket, sys
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
    elif args[args.index("-n") + 1] == "bastion":
        print("/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Network/bastionHosts/testlab-bastion")
    elif args[args.index("-n") + 1] == "build-vm":
        print(json.dumps({"vmId": {"value": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Compute/virtualMachines/testlab-build"},
                          "username": {"value": "labbuilder"}}))
    elif args[args.index("--query") + 1] == "{provisioningState:properties.provisioningState,outputs:properties.outputs}":
        if "TEST_FOUNDATION_RESPONSE" in os.environ:
            sys.stdout.write(os.environ["TEST_FOUNDATION_RESPONSE"])
        else:
            print(json.dumps({"provisioningState": os.environ.get("TEST_FOUNDATION_STATE", "Succeeded"),
                              "outputs": json.loads((root / "outputs.json").read_text())}))
    else:
        print((root / "outputs.json").read_text())
elif args[:2] == ["acr", "login"]:
    print(json.dumps({"loginServer": os.environ.get("TEST_REGISTRY", "testregistry.azurecr.io"), "accessToken": "synthetic-test-token"}))
elif args[:2] == ["aks", "show"]:
    print(os.environ.get("TEST_CLUSTER", json.dumps({
        "apiServerAccessProfile": {"enablePrivateCluster": True, "enablePrivateClusterPublicFqdn": False},
        "disableLocalAccounts": True, "aadProfile": {"managed": True, "enableAzureRbac": True},
        "privateFqdn": "testlab.private.azmk8s.io",
        "id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.ContainerService/managedClusters/testlab-aks",
    })))
elif args[:3] == ["network", "bastion", "show"]:
    print(os.environ.get("TEST_BASTION", json.dumps({
        "sku": {"name": "Standard"}, "enableTunneling": True, "provisioningState": "Succeeded",
        "id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Network/bastionHosts/testlab-bastion",
    })))
elif args[:2] == ["aks", "get-credentials"]:
    assert "--admin" not in args
    cluster = args[args.index("-n") + 1]
    pathlib.Path(args[args.index("--file") + 1]).write_text(json.dumps({
        "cluster": cluster, "context": cluster,
    }))
    sys.exit(int(os.environ.get("TEST_CREDENTIAL_STATUS", "0")))
elif args[:3] == ["aks", "bastion", "tunnel"]:
    if "--help" not in args:
        assert "--admin" not in args
        path = pathlib.Path(args[args.index("--kubeconfig-path") + 1])
        (root / "bastion-call.json").write_text(json.dumps({
            "args": args, "path": str(path), "mode": path.stat().st_mode & 0o777,
            "credential": path.read_text(),
            "converted": (root / "kubelogin-call.json").exists(),
        }))
        sys.exit(int(os.environ.get("TEST_TUNNEL_STATUS", "0")))
elif args[:3] == ["network", "bastion", "tunnel"]:
    if "--help" not in args:
        (root / "network-bastion-call.json").write_text(json.dumps(args))
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(args[args.index("--port") + 1])))
        listener.listen()
        while True:
            connection, _ = listener.accept()
            connection.close()
elif args[:3] == ["network", "bastion", "ssh"]:
    (root / "build-ssh-call.json").write_text(json.dumps(args))
elif args[:4] == ["monitor", "log-analytics", "workspace", "show"]:
    print("synthetic-workspace")
elif args[:1] == ["rest"]:
    print(args[args.index("--body") + 1])
elif args[:3] in (["deployment", "group", "what-if"], ["deployment", "group", "create"]):
    pass
else:
    sys.exit("Unexpected Azure command: " + repr(args))
""")
        self.mock(binary / "kubelogin", """#!/usr/bin/env python3
import json, os, pathlib, sys
(pathlib.Path(os.environ["TEST_ROOT"]) / "kubelogin-call.json").write_text(json.dumps(sys.argv[1:]))
sys.exit(int(os.environ.get("TEST_KUBELOGIN_STATUS", "0")))
""")
        self.mock(binary / "podman", """#!/usr/bin/env python3
import json, os, pathlib, sys
path = pathlib.Path(os.environ["TEST_ROOT"]) / "podman-call.json"
path.write_text(json.dumps({"args":sys.argv[1:], "stdin":sys.stdin.read()}))
print("Login Succeeded!")
""")
        self.mock(binary / "kubectl", """#!/usr/bin/env python3
import json, os, pathlib, re, sys
args = sys.argv[1:]
if args[:1] == ["--kubeconfig"] and args[2:4] == ["config", "rename-context"]:
    path = pathlib.Path(args[1])
    content = json.loads(path.read_text())
    assert content["context"] == args[4]
    content["context"] = args[5]
    path.write_text(json.dumps(content))
    (pathlib.Path(os.environ["TEST_ROOT"]) / "kubectl-rename-call.json").write_text(json.dumps(args))
    sys.exit(0)
if args[:1] == ["--kubeconfig"] and args[2:4] == ["config", "set-cluster"]:
    path = pathlib.Path(args[1])
    content = json.loads(path.read_text())
    assert content["cluster"] == args[4]
    content["server"] = args[args.index(next(item for item in args if item.startswith("--server=")))].split("=", 1)[1]
    content["tls-server-name"] = args[args.index(next(item for item in args if item.startswith("--tls-server-name=")))].split("=", 1)[1]
    path.write_text(json.dumps(content))
    sys.exit(0)
if args[:1] != ["kustomize"]:
    sys.exit("Only offline kustomize and context rename are allowed in these tests.")
root = pathlib.Path(args[1])
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
        result = subprocess.run([self.shell, str(self.root / script), *args],
                                cwd=self.root, env=self.env, input=input,
                                text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_loading_context_and_generated_names(self):
        result = subprocess.run(
            ["bash", "-c", (
                'set +e +u +o pipefail; source scripts/use-lab.sh; '
                '[[ $- != *e* && $- != *u* ]]; [[ $(set -o | awk \'$1 == "pipefail" {print $2}\') == off ]]; '
                'lab_value KeyVaultName; output_value apiClientId'
            )],
            cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["generated-vault", "api-client"])
        self.env["TEST_SUBSCRIPTION"] = ADMIN
        self.run_script("scripts/use-lab.sh", success=False)

    @unittest.skipUnless(shutil.which("zsh"), "Zsh is not installed")
    def test_zsh_loading_preserves_options_and_accessors_after_changing_directory(self):
        for no_foundation in ("0", "1"):
            with self.subTest(no_foundation=no_foundation):
                self.env["TEST_NO_FOUNDATION"] = no_foundation
                result = subprocess.run(
                    ["zsh", "-f", "-c", (
                        'setopt nounset pipefail shwordsplit; before=$(set +o); '
                        'source "$TEST_ROOT/scripts/use-lab.sh" || exit; '
                        '[[ $(set +o) == "$before" ]] || exit 9; '
                        '[[ $Root == "$TEST_ROOT" ]] || exit 8; '
                        'cd /; lab_value ClusterName; '
                        'if [[ $Outputs == "{}" ]]; then printf "{}\\n"; '
                        'else lab_value KeyVaultName; output_value apiClientId; fi'
                    )],
                    cwd=self.root / "scripts", env=self.env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = (["testlab-aks", "{}"] if no_foundation == "1"
                            else ["testlab-aks", "generated-vault", "api-client"])
                self.assertEqual(result.stdout.splitlines(), expected)

    def test_sourcing_errors_preserve_state_and_options_in_supported_shells(self):
        for shell in ("bash", "zsh"):
            if not shutil.which(shell):
                continue
            for failure in ("prefix", "azure", "context", "missing", "json"):
                with self.subTest(shell=shell, failure=failure):
                    settings = dict(self.settings)
                    if failure == "prefix":
                        settings["Prefix"] = "REPLACE"
                    settings_path = self.root / "local.settings.json"
                    settings_path.write_text("{" if failure == "json" else json.dumps(settings))
                    if failure == "missing":
                        settings_path.unlink()
                    env = dict(self.env, TEST_AZ_FAIL="1" if failure == "azure" else "0",
                               TEST_SUBSCRIPTION=ADMIN if failure == "context" else SUBSCRIPTION)
                    result = subprocess.run(
                        [shell, "-c", (
                            'set +e; Root=old-root; Lab=old-lab; Outputs=old-outputs; '
                            'before=$(set +o); source ./scripts/use-lab.sh; result=$?; '
                            '[[ $(set +o) == "$before" ]] || exit 9; '
                            '[[ $Root == old-root && $Lab == old-lab && $Outputs == old-outputs ]] || exit 8; '
                            'printf "status=%s alive=yes\\n" "$result"'
                        )],
                        cwd=self.root, env=env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "status=1 alive=yes\n")
                    self.assertTrue(result.stderr)

    def test_invalid_settings_return_without_exiting_calling_shell(self):
        self.settings["Prefix"] = "REPLACE"
        (self.root / "local.settings.json").write_text(json.dumps(self.settings))
        result = subprocess.run(
            ["bash", "-c", (
                'set +e +u +o pipefail; source scripts/use-lab.sh; status=$?; '
                'printf "status=%s alive=yes errexit=%s nounset=%s pipefail=%s\\n" '
                '"$status" "$([[ $- == *e* ]] && echo on || echo off)" '
                '"$([[ $- == *u* ]] && echo on || echo off)" '
                '"$(set -o | awk \'$1 == "pipefail" {print $2}\')"'
            )],
            cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Prefix must be 4-12", result.stderr)
        self.assertEqual(result.stdout, "status=1 alive=yes errexit=off nounset=off pipefail=off\n")

    def test_invalid_namespace_fails_before_azure(self):
        self.settings["Namespace"] = "other"
        (self.root / "local.settings.json").write_text(json.dumps(self.settings))
        self.run_script("scripts/use-lab.sh", success=False)
        self.assertFalse((self.root / "az-calls.jsonl").exists())

    def test_failed_foundation_without_outputs_keeps_bootstrap_context(self):
        for state, outputs in (("Failed", None), ("Canceled", {})):
            with self.subTest(state=state, outputs=outputs):
                self.env["TEST_FOUNDATION_STATE"] = state
                (self.root / "outputs.json").write_text(json.dumps(outputs))
                result = subprocess.run(
                    [self.shell, "-c", 'source scripts/use-lab.sh || exit; lab_value ClusterName; printf "%s\\n" "$Outputs"; lab_value AcrName'],
                    cwd=self.root, env=self.env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout.splitlines()[:2], ["testlab-aks", "{}"])
                self.assertIn(f"Foundation deployment is {state} and has no outputs", result.stderr)
                self.assertNotIn("--argjson", result.stderr)

    def test_retry_failed_foundation_preserves_apply_confirmation(self):
        self.env["TEST_FOUNDATION_STATE"] = "Failed"
        (self.root / "outputs.json").write_text("null")
        keys = self.root / "rendered/keys"
        keys.mkdir(parents=True)
        (keys / "aks.pub").write_text("ssh-rsa synthetic-public-key\n")
        (keys / "build-vm.pub").write_text("ssh-rsa synthetic-build-public-key\n")
        self.run_script("scripts/deploy-foundation.sh", "--apply", "--confirm", input="wrong-rg\n", success=False)
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertFalse(any(call[:3] == ["deployment", "group", "create"] for call in calls))
        self.run_script("scripts/deploy-foundation.sh", "--apply", "--confirm", input="test-rg\n")
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertEqual(sum(call[:3] == ["deployment", "group", "what-if"] for call in calls), 2)
        self.assertEqual(sum(call[:3] == ["deployment", "group", "create"] for call in calls), 1)

    def test_invalid_or_unfinished_foundation_stops_before_deployment(self):
        keys = self.root / "rendered/keys"
        keys.mkdir(parents=True)
        (keys / "aks.pub").write_text("ssh-rsa synthetic-public-key\n")
        responses = [
            "", "not-json", "null", "[]",
            json.dumps({"provisioningState": "Succeeded", "outputs": None}),
            json.dumps({"provisioningState": "Running", "outputs": None}),
            json.dumps({"provisioningState": "Accepted", "outputs": {}}),
            json.dumps({"provisioningState": "Succeeded", "outputs": []}),
            json.dumps({"provisioningState": "Succeeded", "outputs": {"acrName": {"value": "partial"}}}),
        ]
        for response in responses:
            with self.subTest(response=response):
                self.env["TEST_FOUNDATION_RESPONSE"] = response
                result = self.run_script("scripts/deploy-foundation.sh", "--apply", "--confirm",
                                         input="test-rg\n", success=False)
                self.assertIn("Foundation", result.stderr)
                self.assertNotIn("invalid JSON text passed to --argjson", result.stderr)
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertFalse(any(call[:3] in (["deployment", "group", "what-if"],
                                         ["deployment", "group", "create"]) for call in calls))

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

    def test_flux_images_are_mirrored_once_each(self):
        images = [f"ghcr.io/fluxcd/{name}:v1" for name in (
            "source-controller", "kustomize-controller", "helm-controller", "notification-controller")]
        self.mock(self.root / "bin/flux", """#!/usr/bin/env python3
import json, os, sys
assert sys.argv[1:] == ["install", "--export"]
print(os.environ["TEST_FLUX_MANIFEST"])
""")
        self.mock(self.root / "bin/podman", """#!/usr/bin/env python3
import json, os, pathlib, sys
with (pathlib.Path(os.environ["TEST_ROOT"]) / "mirror-calls.jsonl").open("a") as file:
    file.write(json.dumps(sys.argv[1:]) + "\\n")
""")
        self.env["TEST_FLUX_MANIFEST"] = "\n".join(images + images[:1])
        self.run_script("ops/mirror-flux-images.sh", "--registry-server", "testregistry.azurecr.io")
        calls = [json.loads(line) for line in (self.root / "mirror-calls.jsonl").read_text().splitlines()]
        expected = []
        for image in sorted(images):
            target = image.replace("ghcr.io", "testregistry.azurecr.io")
            expected.extend([["pull", image], ["tag", image, target], ["push", target]])
        self.assertEqual(calls, expected)
        (self.root / "mirror-calls.jsonl").unlink()
        self.env["TEST_FLUX_MANIFEST"] = images[0]
        self.run_script("ops/mirror-flux-images.sh", "--registry-server", "testregistry.azurecr.io", success=False)
        self.assertFalse((self.root / "mirror-calls.jsonl").exists())

    def test_log_query_keeps_query_literal_and_default_timespan(self):
        query = "Usage | where TimeGenerated > ago(24h)"
        result = self.run_script("ops/invoke-logs-query.sh", "--query", query)
        self.assertEqual(json.loads(result.stdout), {"query": query, "timespan": "PT1H"})

    def test_bastion_uses_user_credentials_private_file_and_cleanup(self):
        original = self.root / "original-kubeconfig"
        original.write_text("untouched default kubeconfig")
        self.env["KUBECONFIG"] = str(original)
        for status in (0, 23):
            with self.subTest(tunnel_status=status):
                self.env["TEST_TUNNEL_STATUS"] = str(status)
                result = self.run_script("scripts/connect-aks-bastion.sh", success=status == 0)
                self.assertEqual(result.returncode, status)
                call = json.loads((self.root / "bastion-call.json").read_text())
                self.assertEqual(call["mode"], 0o600)
                self.assertNotIn(" ", call["path"])
                credential = json.loads(call["credential"])
                self.assertEqual(credential, {
                    "cluster": "testlab-aks", "context": "testlab-aks-bastion",
                })
                self.assertTrue(call["converted"])
                self.assertNotIn("--admin", call["args"])
                self.assertEqual(call["args"][call["args"].index("--port") + 1], "8443")
                self.assertEqual(call["args"][call["args"].index("--bastion") + 1], BASTION_ID)
                self.assertFalse(pathlib.Path(call["path"]).exists())
                self.assertEqual(original.read_text(), "untouched default kubeconfig")
                self.assertNotIn(call["credential"], result.stdout + result.stderr)
                conversion = json.loads((self.root / "kubelogin-call.json").read_text())
                self.assertEqual(conversion, ["convert-kubeconfig", "--kubeconfig", call["path"],
                                              "--login", "azurecli"])
                calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
                credentials = [args for args in calls if args[:2] == ["aks", "get-credentials"]]
                self.assertTrue(all("--file" in args and "--admin" not in args for args in credentials))
                self.assertTrue(all("--context" not in args for args in credentials))
                rename = json.loads((self.root / "kubectl-rename-call.json").read_text())
                self.assertEqual(rename[2:], [
                    "config", "rename-context", "testlab-aks", "testlab-aks-bastion",
                ])
                self.assertFalse(any(args[:3] in (["deployment", "group", "create"],
                                                  ["network", "bastion", "create"]) for args in calls))

    def test_bastion_explicit_target_needs_no_foundation_reads(self):
        self.run_script("scripts/connect-aks-bastion.sh", "--resource-group", "secondary-rg",
                        "--cluster-name", "secondary-aks", "--bastion-id", BASTION_ID,
                        "--context", "secondary-aks-secondary", "--port", "08444")
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertFalse(any(args[:1] == ["deployment"] for args in calls))
        credentials = next(args for args in calls if args[:2] == ["aks", "get-credentials"])
        self.assertEqual(credentials[credentials.index("-n") + 1], "secondary-aks")
        self.assertNotIn("--context", credentials)
        rename = json.loads((self.root / "kubectl-rename-call.json").read_text())
        self.assertEqual(rename[2:], [
            "config", "rename-context", "secondary-aks", "secondary-aks-secondary",
        ])
        call = json.loads((self.root / "bastion-call.json").read_text())
        self.assertEqual(json.loads(call["credential"]), {
            "cluster": "secondary-aks", "context": "secondary-aks-secondary",
        })
        self.assertEqual(call["args"][call["args"].index("--port") + 1], "8444")
        self.assertFalse(pathlib.Path(call["path"]).exists())

    def test_bastion_activation_uses_current_shell_and_restores_kubeconfig(self):
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.env["TEST_ACTIVATION_PORT"] = str(port)
        original = self.root / "original-kubeconfig"
        original.write_text("untouched default kubeconfig")
        self.env["KUBECONFIG"] = str(original)
        command = """
helper=$1
set --
source "$helper" --port "$TEST_ACTIVATION_PORT" || exit
printf '%s' "$KUBECONFIG" > "$TEST_ROOT/active-kubeconfig-path"
cp "$KUBECONFIG" "$TEST_ROOT/active-kubeconfig-copy"
printf '%s' "$AKS_BASTION_LOG" > "$TEST_ROOT/active-log-path"
kill -0 "$AKS_BASTION_PID"
disconnect_aks_bastion || exit
printf '%s' "$KUBECONFIG" > "$TEST_ROOT/restored-kubeconfig"
[[ -z ${AKS_BASTION_PID:-} ]]
"""
        result = subprocess.run(
            [self.shell, "-c", command, "activate-test",
             str(self.root / "scripts/activate-aks-bastion.sh")],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        active_path = pathlib.Path((self.root / "active-kubeconfig-path").read_text())
        log_path = pathlib.Path((self.root / "active-log-path").read_text())
        self.assertFalse(active_path.exists())
        self.assertFalse(log_path.exists())
        self.assertEqual((self.root / "restored-kubeconfig").read_text(), str(original))
        self.assertEqual(original.read_text(), "untouched default kubeconfig")
        active = json.loads((self.root / "active-kubeconfig-copy").read_text())
        self.assertEqual(active, {
            "cluster": "testlab-aks",
            "context": "testlab-aks-bastion",
            "server": f"https://localhost:{port}/",
            "tls-server-name": "testlab.private.azmk8s.io",
        })
        call = json.loads((self.root / "network-bastion-call.json").read_text())
        self.assertEqual(call[call.index("--target-resource-id") + 1],
                         "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.ContainerService/managedClusters/testlab-aks")
        self.assertEqual(call[call.index("--resource-port") + 1], "443")
        self.assertEqual(call[call.index("--port") + 1], str(port))
        self.assertIn("Run disconnect_aks_bastion when finished.", result.stdout)

    def test_bastion_activation_must_be_sourced(self):
        result = self.run_script("scripts/activate-aks-bastion.sh", success=False)
        self.assertIn("Source this helper", result.stderr)

    def test_bastion_rejects_insecure_clusters_and_unready_hosts(self):
        cluster = {
            "apiServerAccessProfile": {"enablePrivateCluster": True, "enablePrivateClusterPublicFqdn": False},
            "disableLocalAccounts": True, "aadProfile": {"managed": True, "enableAzureRbac": True},
            "privateFqdn": "testlab.private.azmk8s.io",
        }
        bastion = {"sku": {"name": "Standard"}, "enableTunneling": True,
                   "provisioningState": "Succeeded"}
        targets = [
            ({**cluster, "disableLocalAccounts": False}, bastion),
            ({**cluster, "apiServerAccessProfile": {"enablePrivateCluster": False,
                                                  "enablePrivateClusterPublicFqdn": False}}, bastion),
            ({**cluster, "apiServerAccessProfile": {"enablePrivateCluster": True,
                                                  "enablePrivateClusterPublicFqdn": True}}, bastion),
            ({**cluster, "aadProfile": {"managed": True, "enableAzureRbac": False}}, bastion),
            (cluster, {**bastion, "sku": {"name": "Basic"}}),
            (cluster, {**bastion, "enableTunneling": False}),
            (cluster, {**bastion, "provisioningState": "Failed"}),
        ]
        for unsafe_cluster, unsafe_bastion in targets:
            with self.subTest(cluster=unsafe_cluster, bastion=unsafe_bastion):
                self.env["TEST_CLUSTER"] = json.dumps(unsafe_cluster)
                self.env["TEST_BASTION"] = json.dumps(unsafe_bastion)
                self.run_script("scripts/connect-aks-bastion.sh", success=False)
        self.assertFalse((self.root / "bastion-call.json").exists())
        self.assertFalse((self.root / "kubelogin-call.json").exists())

    def test_bastion_accepts_arm_casing_and_premium(self):
        self.env["TEST_CLUSTER"] = json.dumps({
            "apiServerAccessProfile": {"enablePrivateCluster": True, "enablePrivateClusterPublicFQDN": False},
            "disableLocalAccounts": True, "aadProfile": {"managed": True, "enableAzureRBAC": True},
            "privateFqdn": "testlab.private.azmk8s.io",
        })
        self.env["TEST_BASTION"] = json.dumps({
            "sku": {"name": "Premium"}, "enableTunneling": True, "provisioningState": "Succeeded",
        })
        self.run_script("scripts/connect-aks-bastion.sh")
        call = json.loads((self.root / "bastion-call.json").read_text())
        self.assertFalse(pathlib.Path(call["path"]).exists())

    def test_bastion_cleans_up_when_credential_preparation_fails(self):
        for credential_status, conversion_status in ((17, 0), (0, 19)):
            with self.subTest(credentials=credential_status, conversion=conversion_status):
                self.env["TEST_CREDENTIAL_STATUS"] = str(credential_status)
                self.env["TEST_KUBELOGIN_STATUS"] = str(conversion_status)
                result = self.run_script("scripts/connect-aks-bastion.sh", success=False)
                self.assertEqual(result.returncode, credential_status or conversion_status)
                calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
                credentials = next(args for args in reversed(calls) if args[:2] == ["aks", "get-credentials"])
                self.assertFalse(pathlib.Path(credentials[credentials.index("--file") + 1]).exists())
                self.assertFalse((self.root / "bastion-call.json").exists())

    def test_bastion_invalid_arguments_fail_before_azure(self):
        for options in (("--port", "443"), ("--port", "65536"),
                        ("--port", "18446744073709560059"),
                        ("--resource-group", "partial-target"),
                        ("--context", "bad context"),
                        ("--resource-group", "test-rg", "--cluster-name", "testlab-aks",
                         "--bastion-id", "/wrong/resource")):
            with self.subTest(options=options):
                self.run_script("scripts/connect-aks-bastion.sh", *options, success=False)
        self.assertFalse((self.root / "bastion-call.json").exists())
        self.assertFalse((self.root / "az-calls.jsonl").exists())

    def test_log_query_preserves_explicit_and_default_timespans(self):
        query = "Usage | where TimeGenerated > ago(24h)"
        for options, expected in (((), "PT1H"), (("--timespan", "P1D"), "P1D"),
                                  (("--timespan", "PT30M"), "PT30M")):
            with self.subTest(options=options):
                result = self.run_script("ops/invoke-logs-query.sh", "--query", query, *options)
                self.assertEqual(json.loads(result.stdout), {"query": query, "timespan": expected})

    def test_invalid_log_query_timespans_fail_before_azure(self):
        for duration in ("", "P0D", "PT0H", "P-1D", "24h", "P1D; echo unsafe"):
            with self.subTest(duration=duration):
                self.run_script("ops/invoke-logs-query.sh", "--query", "Usage",
                                "--timespan", duration, success=False)
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

    def test_renderer_refuses_accumulated_and_gitops_owned_state(self):
        source = self.root / "rendered/base"
        source.mkdir(parents=True)
        sentinel = source / "kustomization.yaml"
        sentinel.write_text("retained CSI and network state\n")
        result = self.run_script("scripts/render-manifests.sh", success=False)
        self.assertIn("Existing rendered/base", result.stderr)
        self.assertEqual(sentinel.read_text(), "retained CSI and network state\n")
        sentinel.unlink()
        source.rmdir()
        target = self.root / "gitops/clusters/primary/apps/orders"
        target.mkdir(parents=True)
        (target / "kustomization.yaml").write_text("authoritative Git state\n")
        result = self.run_script("scripts/render-manifests.sh", success=False)
        self.assertIn("GitOps already owns", result.stderr)
        self.assertFalse(source.exists())

    def test_foundation_is_what_if_by_default(self):
        keys = self.root / "rendered/keys"
        keys.mkdir(parents=True)
        (keys / "aks.pub").write_text("ssh-rsa synthetic-public-key")
        (keys / "build-vm.pub").write_text("ssh-rsa synthetic-build-public-key")
        self.run_script("scripts/deploy-foundation.sh")
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertTrue(any(call[:3] == ["deployment", "group", "what-if"] for call in calls))
        self.assertFalse(any(call[:3] == ["deployment", "group", "create"] for call in calls))
        self.run_script("scripts/deploy-foundation.sh", "--apply", success=False)
        self.run_script("scripts/deploy-foundation.sh", "--apply", "--confirm", input="wrong-group\n", success=False)
        self.run_script("scripts/deploy-foundation.sh", "--apply", "--confirm", input="test-rg\n")
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertEqual(sum(call[:3] == ["deployment", "group", "create"] for call in calls), 1)
        what_if = next(call for call in calls if call[:3] == ["deployment", "group", "what-if"])
        self.assertIn("sshPublicKey=ssh-rsa synthetic-public-key", what_if)
        self.assertIn("buildVmSshPublicKey=ssh-rsa synthetic-build-public-key", what_if)
        self.assertIn("buildVmSize=Standard_B2s", what_if)

    def test_foundation_requires_separate_public_build_key(self):
        keys = self.root / "rendered/keys"
        keys.mkdir(parents=True)
        (keys / "aks.pub").write_text("ssh-rsa synthetic-public-key")
        result = self.run_script("scripts/deploy-foundation.sh", success=False)
        self.assertIn("separate rendered/keys/build-vm.pub", result.stderr)
        (keys / "build-vm.pub").write_text("-----BEGIN PRIVATE KEY-----")
        self.run_script("scripts/deploy-foundation.sh", success=False)
        calls = [json.loads(line) for line in (self.root / "az-calls.jsonl").read_text().splitlines()]
        self.assertFalse(any(args[:3] == ["deployment", "group", "what-if"] for args in calls))

    def test_build_vm_connection_uses_bastion_and_local_private_key(self):
        keys = self.root / "rendered/keys"
        keys.mkdir(parents=True)
        self.run_script("scripts/connect-build-vm.sh", success=False)
        (keys / "build-vm").write_text("synthetic-private-key")
        self.run_script("scripts/connect-build-vm.sh")
        call = json.loads((self.root / "build-ssh-call.json").read_text())
        self.assertEqual(call[call.index("--ids") + 1], BASTION_ID)
        self.assertEqual(call[call.index("--auth-type") + 1], "ssh-key")
        self.assertEqual(call[call.index("--username") + 1], "labbuilder")
        self.assertEqual(call[call.index("--ssh-key") + 1], str(keys / "build-vm"))
        self.assertIn("/providers/Microsoft.Compute/virtualMachines/testlab-build",
                      call[call.index("--target-resource-id") + 1])

    def test_build_vm_size_defaults_and_rejects_invalid_settings(self):
        for size in ("Standard_D2s_v5", "", False, None, "B2s", "Standard_B2s; echo invalid"):
            with self.subTest(size=size):
                settings = {**self.settings, "BuildVmSize": size}
                (self.root / "local.settings.json").write_text(json.dumps(settings))
                result = subprocess.run([self.shell, "-c", "source scripts/use-lab.sh || exit; lab_value BuildVmSize"],
                                        cwd=self.root, env=self.env, text=True, capture_output=True)
                if size == "Standard_D2s_v5":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), size)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("BuildVmSize", result.stderr)

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


@unittest.skipUnless(shutil.which("zsh"), "Zsh is not installed")
class ZshHelpersTests(BashHelpersTests):
    shell = "zsh"


if __name__ == "__main__":
    unittest.main()
