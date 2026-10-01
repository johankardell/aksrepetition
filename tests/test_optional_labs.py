import base64
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest


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
        for guide in ("12-rabbitmq-container-storage.md", "13-confidential-compute.md"):
            block = bash_block(guide, "GroupExists=$(az group exists")
            guard = block[block.index("GroupExists=$("):block.index("\nprintf 'SubscriptionId=")]
            for exists, status in (("false", "0"), ("true", "0"), ("", "22"), ("false", "22")):
                with self.subTest(guide=guide, exists=exists, status=status):
                    result = self.run_bash(
                        'az() { printf "%s" "$Exists"; return "$Status"; }\n' + guard,
                        Exists=exists, Status=status, ResourceGroup="rg-test",
                    )
                    self.assertEqual(result.returncode == 0, exists == "false" and status == "0")

    def test_confidential_creation_uses_create_taint_flag_and_one_node_ceiling(self):
        create = bash_block("13-confidential-compute.md", "az aks create ")
        self.assertIn("--nodepool-taints CriticalAddonsOnly=true:NoSchedule", create)
        self.assertNotIn("--node-taints ", create)
        guide = (ROOT / "labs/13-confidential-compute.md").read_text()
        self.assertEqual(re.findall(r"--min-count (\d+) --max-count (\d+)", guide),
                         [("0", "1"), ("0", "1")])

    def test_manual_cvm_recovery_only_runs_after_autoscaler_timeout(self):
        block = bash_block("13-confidential-compute.md",
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
        block = bash_block("13-confidential-compute.md", "CvmPool=$(jq")
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
        guide = "12-rabbitmq-container-storage.md"
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
        block = bash_block("12-rabbitmq-container-storage.md",
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
