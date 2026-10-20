import base64
import contextlib
import errno
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]


def bash_block(guide, marker):
    blocks = re.findall(r"^```bash\n(.*?)^```", (ROOT / "labs" / guide).read_text(),
                        re.M | re.S)
    matches = [block for block in blocks if marker in block]
    if len(matches) != 1:
        raise AssertionError(f"Expected one Bash block containing {marker!r}")
    return matches[0]


class OptionalLabsTests(unittest.TestCase):
    def run_bash(self, script, **environment):
        return subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + script],
            env={**os.environ, **environment}, text=True, capture_output=True,
            timeout=10,
        )

    def test_resource_group_gate_rejects_reuse_and_azure_errors(self):
        for guide in ("25-rabbitmq-container-storage.md", "26-confidential-compute.md",
                      "28-overlay-overlap.md"):
            block = bash_block(guide, 'if [[ "$GroupExists" != false ]]')
            guard = block[block.index("GroupExists=$("):block.index("\nprintf 'SubscriptionId=")]
            for exists, status in (("false", "0"), ("true", "0"), ("", "22"), ("false", "22")):
                with self.subTest(guide=guide, exists=exists, status=status):
                    result = self.run_bash(
                        'az() { printf "%s" "$Exists"; return "$Status"; }\n' + guard,
                        Exists=exists, Status=status, ResourceGroup="rg-test",
                    )
                    self.assertEqual(result.returncode == 0, exists == "false" and status == "0")

    def test_overlap_address_selection_rejects_reserved_and_unplanned_collisions(self):
        block = bash_block("28-overlay-overlap.md", "PeerCidr=$(python3")
        code = re.search(r"PeerCidr=.*?<<'PY'\n(.*?)^PY$", block, re.M | re.S)[1]
        for address, recovery, valid in (
            ("192.168.2.10", "10.81.0.0/24", True),
            ("192.168.2.254", "10.81.0.0/24", True),
            ("192.168.2.0", "10.81.0.0/24", False),
            ("192.168.2.3", "10.81.0.0/24", False),
            ("192.168.2.255", "10.81.0.0/24", False),
            ("172.28.2.10", "10.81.0.0/24", False),
            ("192.168.2.10", "192.168.50.0/24", False),
            ("192.168.2.10", "10.80.16.0/24", False),
        ):
            with self.subTest(address=address, recovery=recovery):
                result = subprocess.run(
                    [sys.executable, "-", address, "192.168.0.0/16", "10.80.0.0/16",
                     recovery, "172.29.0.0/16"],
                    input=code, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                if valid:
                    self.assertEqual(result.stdout.strip(), "192.168.2.0/24")
                else:
                    self.assertEqual(result.stdout, "")

    def test_overlap_probe_accepts_only_the_expected_service_or_network_failure(self):
        block = bash_block("28-overlay-overlap.md", 'cat > "$Work/probe.py"')
        code = re.search(r"<<'PY'\n(.*?)^PY$", block, re.M | re.S)[1]
        marker = "lab28-0123456789abcdef"
        refused = urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))
        http_error = urllib.error.HTTPError("http://192.168.2.10:8080/", 503, "unavailable", {}, None)
        cases = [
            ("reachable", marker, 200, None, True),
            ("reachable", "wrong-server", 200, None, False),
            ("unreachable", marker, 200, None, False),
            ("unreachable", "wrong-server", 200, None, False),
            ("unreachable", "", 503, http_error, False),
            ("unreachable", "", 0, refused, True),
            ("reachable", "", 0, refused, False),
            ("unreachable", "", 0, TimeoutError("timed out"), True),
            ("unreachable", "", 0, urllib.error.URLError("unclassified failure"), False),
        ]
        for expected, body, status, error, valid in cases:
            with self.subTest(expected=expected, body=body, error=error):
                opener = MagicMock()
                if error:
                    opener.open.side_effect = error
                else:
                    response = opener.open.return_value.__enter__.return_value
                    response.status = status
                    response.read.return_value = f"{body}\n".encode()
                output = io.StringIO()
                with patch("sys.argv", ["probe.py", "192.168.2.10", "8080", marker, expected]), \
                        patch("urllib.request.build_opener", return_value=opener), \
                        contextlib.redirect_stdout(output):
                    if valid:
                        exec(compile(code, "overlap-probe.py", "exec"), {})
                    else:
                        with self.assertRaises((SystemExit, urllib.error.URLError, TimeoutError)):
                            exec(compile(code, "overlap-probe.py", "exec"), {})
                if valid:
                    self.assertIn("EXPECTED_NETWORK_FAILURE:" if error else f"SERVICE_OK:{marker}",
                                  output.getvalue())
                else:
                    self.assertNotIn("SERVICE_OK:", output.getvalue())
                    self.assertNotIn("EXPECTED_NETWORK_FAILURE:", output.getvalue())

    def test_overlap_group_guard_rejects_wrong_context_and_ownership(self):
        block = bash_block("28-overlay-overlap.md", "assert_lab()")
        mocks = """
az() {
  if [[ "$*" == "account show "* ]]; then printf '%s' "$Account"; return "$AzureStatus"
  elif [[ "$*" == "group show "* ]]; then printf '%s' "$Group"; return "$AzureStatus"
  else return 99
  fi
}
"""
        account = {"id": "subscription", "tenantId": "tenant"}
        group = {"tags": {"purpose": "aks-overlay-overlap-lab", "run": "lab28-marker"}}
        for problem in ("none", "subscription", "tenant", "purpose", "run", "azure-error", "malformed"):
            context = dict(account)
            resource = json.loads(json.dumps(group))
            if problem == "subscription":
                context["id"] = "other"
            elif problem == "tenant":
                context["tenantId"] = "other"
            elif problem in ("purpose", "run"):
                resource["tags"][problem] = "other"
            with self.subTest(problem=problem):
                result = self.run_bash(
                    mocks + block, Account="invalid-json" if problem == "malformed" else json.dumps(context),
                    Group=json.dumps(resource), AzureStatus="22" if problem == "azure-error" else "0",
                    SubscriptionId="subscription", TenantId="tenant", ResourceGroup="rg-test",
                    Marker="lab28-marker",
                )
                self.assertEqual(result.returncode == 0, problem == "none", result.stderr)

    def test_overlap_control_requires_remote_marker_not_just_run_command_success(self):
        block = bash_block("28-overlay-overlap.md", "probe_control()")
        function = re.search(r"probe_control\(\) \{.*?\n\}", block, re.S)[0]
        marker = "lab28-0123456789abcdef"
        mocks = """
assert_lab() { return 0; }
az() { printf '%s' "$Result"; return "$AzureStatus"; }
"""
        with tempfile.TemporaryDirectory() as folder:
            (pathlib.Path(folder) / "probe.py").write_text("print('synthetic probe')\n")
            for message, status, valid in (
                (f"[stdout]\nSERVICE_OK:{marker}\n", "0", True),
                ("Provisioning succeeded", "0", False),
                ("SERVICE_OK:wrong-marker", "0", False),
                (f"SERVICE_OK:{marker}", "22", False),
            ):
                with self.subTest(message=message, status=status):
                    result = self.run_bash(
                        mocks + function + "\nprobe_control 192.168.2.10\n",
                        Work=folder, Marker=marker, ResourceGroup="rg-test",
                        Result=json.dumps({"value": [{"message": message}]}), AzureStatus=status,
                    )
                    self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_overlap_collision_guard_rejects_replaced_or_moved_pod(self):
        block = bash_block("28-overlay-overlap.md", "assert_probe_identity()")
        function = re.search(r"assert_probe_identity\(\) \{.*?\n\}", block, re.S)[0]
        mocks = """
assert_cluster() { return 0; }
kubectl() { printf '%s' "$Pod"; }
"""
        for uid, address, phase, valid in (
            ("original", "192.168.2.10", "Running", True),
            ("replacement", "192.168.2.10", "Running", False),
            ("original", "192.168.2.11", "Running", False),
            ("original", "192.168.2.10", "Failed", False),
        ):
            with self.subTest(uid=uid, address=address, phase=phase):
                result = self.run_bash(
                    mocks + function + "\nassert_probe_identity\n",
                    Pod=json.dumps({"metadata": {"uid": uid}, "status": {"podIP": address, "phase": phase}}),
                    PodUid="original", PodIp="192.168.2.10",
                )
                self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_overlap_recovery_stops_before_removing_network_when_nic_move_fails(self):
        block = bash_block("28-overlay-overlap.md", "az vm deallocate ")
        mocks = """
assert_probe_identity() { return 0; }
az() {
  if [[ "$*" == "vm show "* ]]; then printf '%s' "$ServiceVmId"; return 0; fi
  printf '%s\\n' "$*"
  if [[ "$*" == "network nic ip-config update "* ]]; then return "$MoveStatus"; fi
}
"""
        for status in ("0", "29"):
            with self.subTest(move_status=status):
                result = subprocess.run(
                    ["bash", "-c", "set -euo pipefail\n" + mocks + block],
                    env={**os.environ, "MoveStatus": status, "ResourceGroup": "rg-test",
                         "ServiceVmId": "vm-identity", "ServiceIpConfig": "primary",
                         "RecoverySubnetId": "/synthetic/subnets/recovery", "RecoveryIp": "10.81.0.10",
                         "RecoveryCidr": "10.81.0.0/24"},
                    input="rg-test\n", text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode, int(status), result.stderr)
                self.assertIn("vm deallocate", result.stdout)
                self.assertIn("--subnet /synthetic/subnets/recovery --private-ip-address 10.81.0.10",
                              result.stdout)
                self.assertEqual("vm start" in result.stdout, status == "0")
                self.assertEqual("subnet delete" in result.stdout, status == "0")
                self.assertEqual("vnet update" in result.stdout, status == "0")
                self.assertEqual(result.stdout.count("peering sync"), 2 if status == "0" else 0)

    def test_overlap_cleanup_requires_group_guard_and_exact_confirmation(self):
        block = bash_block("28-overlay-overlap.md", 'if [[ -n ${AKS_BASTION_PID:-} ]]')
        mocks = """
assert_lab() { return "$GuardStatus"; }
az() {
  if [[ "$*" == "group delete "* ]]; then printf 'DELETE:%s\\n' "$*"
  elif [[ "$*" == "group exists "* ]]; then printf '%s' "$GroupExistsAfter"
  fi
}
"""
        for guard, approval, exists, valid in (
            ("0", "rg-test", "false", True),
            ("17", "rg-test", "false", False),
            ("0", "other-group", "false", False),
            ("0", "rg-test", "true", False),
        ):
            with self.subTest(guard=guard, approval=approval, exists=exists):
                result = subprocess.run(
                    ["bash", "-c", "set -euo pipefail\n" + mocks + block],
                    env={**os.environ, "GuardStatus": guard, "GroupExistsAfter": exists,
                         "ResourceGroup": "rg-test", "AKS_BASTION_PID": ""},
                    input=approval + "\n", text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                self.assertEqual("DELETE:" in result.stdout, guard == "0" and approval == "rg-test")

    def test_confidential_creation_uses_create_taint_flag_and_one_node_ceiling(self):
        create = bash_block("26-confidential-compute.md", "az aks create ")
        self.assertIn("--nodepool-taints CriticalAddonsOnly=true:NoSchedule", create)
        self.assertNotIn("--node-taints ", create)
        guide = (ROOT / "labs/26-confidential-compute.md").read_text()
        self.assertEqual(re.findall(r"--min-count (\d+) --max-count (\d+)", guide),
                         [("0", "1"), ("0", "1")])

    def test_manual_cvm_recovery_only_runs_after_autoscaler_timeout(self):
        block = bash_block("26-confidential-compute.md",
                           'if [[ "$ScaleFromZeroReady" == false ]]')
        mocks = 'az() { printf "az %s\\n" "$*"; }\nkubectl() { printf "kubectl %s\\n" "$*"; }\n'
        for ready in ("true", "false"):
            with self.subTest(ready=ready):
                result = self.run_bash(mocks + block, ScaleFromZeroReady=ready,
                                       ResourceGroup="rg-test", ClusterName="aks-test")
                self.assertEqual(result.returncode, 0, result.stderr)
                if ready == "true":
                    self.assertEqual(result.stdout, "")
                else:
                    self.assertIn("--disable-cluster-autoscaler", result.stdout)
                    self.assertIn("--node-count 1", result.stdout)
                    self.assertIn("--timeout=600s", result.stdout)
                    self.assertEqual(len(result.stdout.splitlines()), 3)

    def test_cvm_cleanup_handles_missing_and_manually_scaled_pools(self):
        block = bash_block("26-confidential-compute.md", "CvmPool=$(jq")
        mocks = """
az() {
  if [[ "$*" == "aks nodepool list "* ]]; then
    printf '%s\\n' "$PoolJson"
  else
    printf 'az %s\\n' "$*"
  fi
}
kubectl() { printf 'kubectl %s\\n' "$*"; }
"""
        cases = [
            ([], False, False),
            ([{"name": "ordinary"}], False, False),
            ([{"name": "cvm", "enableAutoScaling": False}], False, True),
            ([{"name": "cvm", "enableAutoScaling": True}], True, True),
        ]
        for pools, disable, scale in cases:
            with self.subTest(pools=pools):
                result = self.run_bash(mocks + block, PoolJson=json.dumps(pools),
                                       ResourceGroup="rg-test", ClusterName="aks-test")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("--disable-cluster-autoscaler" in result.stdout, disable)
                self.assertEqual("--node-count 0" in result.stdout, scale)
                self.assertIn("az resource list", result.stdout)
        for pools in ('[{"name":"cvm"},{"name":"cvm"}]', "not-json", "", "null", "{}"):
            with self.subTest(invalid=pools):
                result = self.run_bash(mocks + block, PoolJson=pools,
                                       ResourceGroup="rg-test", ClusterName="aks-test")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("nodepool scale", result.stdout)
                self.assertNotIn("resource list", result.stdout)

    def test_rabbitmq_publishes_five_messages_and_preserves_pipeline_failures(self):
        guide = "25-rabbitmq-container-storage.md"
        baseline = bash_block(guide, "rabbit_api() {")
        local = bash_block(guide, "RabbitUser=$(kubectl get secret rabbit-local-default-user")
        function = baseline[baseline.index("rabbit_api() {"):baseline.index("\nrabbit_api /api/")]
        mocks = """
kubectl() {
  if [[ "$*" == *".data.username"* ]]; then printf '%s' "$EncodedUser"
  elif [[ "$*" == *".data.password"* ]]; then printf '%s' "$EncodedPassword"
  fi
}
az() {
  if [[ "$*" == "aks show "* ]]; then printf 'node-rg\\n'
  else printf '0\\n'
  fi
}
curl() {
  cat >> "$AuthFile"
  printf '%s\\n' CALL "$@" >> "$Calls"
  printf '{"routed":%s}\\n' "$Routed"
  return "$CurlExit"
}
"""
        for backend, block in (("baseline", baseline), ("local", function + "\n" + local)):
            for routed, status in (("true", "0"), ("false", "0"), ("true", "22")):
                with self.subTest(backend=backend, routed=routed, status=status):
                    with tempfile.TemporaryDirectory() as folder:
                        calls = pathlib.Path(folder) / "calls"
                        auth = pathlib.Path(folder) / "auth"
                        result = self.run_bash(
                            mocks + block, Calls=str(calls), AuthFile=str(auth),
                            EncodedUser=base64.b64encode(b"synthetic-user").decode(),
                            EncodedPassword=base64.b64encode(b"synthetic-password").decode(),
                            Routed=routed, CurlExit=status,
                            ResourceGroup="rg-test", ClusterName="aks-test",
                        )
                        arguments = calls.read_text()
                        self.assertNotIn("synthetic-user", arguments)
                        self.assertNotIn("synthetic-password", arguments)
                        token = base64.b64encode(b"synthetic-user:synthetic-password").decode()
                        self.assertIn(f"Authorization: Basic {token}", auth.read_text())
                        if routed == "true" and status == "0":
                            self.assertEqual(result.returncode, 0, result.stderr)
                            invocations = arguments.split("CALL\n")[1:]
                            self.assertEqual(len(invocations), 6)
                            queue_args = invocations[0].splitlines()
                            queue = json.loads(queue_args[queue_args.index("--data") + 1])
                            self.assertTrue(queue["durable"])
                            self.assertEqual(queue["arguments"]["x-queue-type"], "quorum")
                            for number, invocation in enumerate(invocations[1:], 1):
                                args = invocation.splitlines()
                                self.assertEqual(args[args.index("--request") + 1], "PUT")
                                payload = json.loads(args[args.index("--data") + 1])
                                self.assertEqual(payload["payload"], f"synthetic-{number}")
                                self.assertEqual(payload["properties"]["delivery_mode"], 2)
                        else:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertLessEqual(arguments.count("CALL\n"), 2)

    def test_baseline_cleanup_waits_only_for_remaining_pods(self):
        block = bash_block("25-rabbitmq-container-storage.md",
                           'mapfile -t BaselinePvcs < "$Work/baseline-pvcs.txt"')
        mocks = """
az() { printf 'node-rg\\n'; }
kubectl() {
  if [[ "$*" == "get pod "* ]]; then printf '%s' "$RemainingPods"
  else printf 'kubectl %s\\n' "$*"
  fi
}
"""
        with tempfile.TemporaryDirectory() as folder:
            (pathlib.Path(folder) / "baseline-pvcs.txt").write_text("claim-0\nclaim-1\nclaim-2\n")
            for pods in ("", "pod/rabbit-disk-server-0\npod/rabbit-disk-server-1\n"):
                with self.subTest(pods=pods):
                    result = self.run_bash(mocks + block, Work=folder, RemainingPods=pods,
                                           ResourceGroup="rg-test", ClusterName="aks-test")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual("kubectl wait" in result.stdout, bool(pods))
                    for number in range(3):
                        self.assertIn(f"delete pvc claim-{number}", result.stdout)
                    self.assertEqual(result.stdout.count("--ignore-not-found"), 4)


if __name__ == "__main__":
    unittest.main()
