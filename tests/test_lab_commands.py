import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDES = [ROOT / "README.md", ROOT / ".github/copilot-instructions.md", *sorted((ROOT / "labs").glob("*.md"))]


class LabCommandsTests(unittest.TestCase):
    @staticmethod
    def bash_block(guide, marker):
        blocks = re.findall(r"^```bash\n(.*?)^```", (ROOT / guide).read_text(), re.M | re.S)
        matches = [block for block in blocks if marker in block]
        if len(matches) != 1:
            raise AssertionError(f"Expected one Bash block containing {marker!r} in {guide}")
        return matches[0]

    def test_bash_examples_parse_without_execution(self):
        for guide in GUIDES:
            for block in re.finditer(r"^```bash\n(.*?)^```", guide.read_text(), re.M | re.S):
                line = guide.read_text()[:block.start()].count("\n") + 1
                with self.subTest(guide=guide.relative_to(ROOT), line=line):
                    result = subprocess.run(["bash", "-n"], input=block[1],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_guide_embedded_python_parses_without_execution(self):
        for guide in GUIDES:
            for block in re.findall(r"<<'PY'[^\n]*\n(.*?)^PY$", guide.read_text(), re.M | re.S):
                with self.subTest(guide=guide.relative_to(ROOT)):
                    compile(block, str(guide), "exec")

    def test_expected_failures_preserve_strict_session_and_reject_wrong_evidence(self):
        examples = [
            ("labs/02-identity.md", 'Denial=$(kubectl exec -n orders "$workerPod"',
             "azure.core.exceptions.HttpResponseError: (Forbidden) Status: 403"),
            ("labs/03-networking.md", "'http://order-api/readyz'",
             "urllib.error.URLError: <urlopen error timed out>"),
            ("labs/03-networking.md", "'https://example.org'",
             "ConnectionResetError: Connection reset by peer"),
            ("labs/04-gitops.md", "Reconciliation=$(flux reconcile",
             "context deadline exceeded"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory)
            stub = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["get", "kustomization"]:
    reason = "HealthCheckFailed" if os.environ["MODE"] == "expected" else "ReconciliationFailed"
    print(json.dumps({"metadata": {"generation": 2}, "status": {"conditions": [
        {"type": "Ready", "status": "False", "reason": reason, "observedGeneration": 2}]}}))
elif args[:2] == ["get", "pod"]:
    print("synthetic-pod")
else:
    mode = os.environ["MODE"]
    print(os.environ["EVIDENCE"] if mode == "expected" else "Name or service not known", file=sys.stderr)
    sys.exit(0 if mode == "unexpected-success" else 37)
"""
            for name in ("kubectl", "flux"):
                path = binary / name
                path.write_text(stub)
                path.chmod(0o755)
            for guide, marker, evidence in examples:
                block = self.bash_block(guide, marker)
                for mode in ("expected", "wrong-evidence", "unexpected-success"):
                    with self.subTest(guide=guide, marker=marker, mode=mode):
                        env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}",
                                   MODE=mode, EVIDENCE=evidence)
                        script = ('set -euo pipefail\nworkerPod=synthetic-pod\ncheck=synthetic-check\n'
                                  + block + '\nprintf "SESSION_PRESERVED\\n"\n')
                        result = subprocess.run(["bash", "-c", script], env=env,
                                                text=True, capture_output=True)
                        if mode == "expected":
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertIn("SESSION_PRESERVED", result.stdout)
                        else:
                            self.assertNotEqual(result.returncode, 0, result.stdout)
                            self.assertNotIn("SESSION_PRESERVED", result.stdout)

    def test_dns_fault_restores_worker_even_when_diagnostics_fail(self):
        block = self.bash_block("labs/03-networking.md", 'worker=\'\'')
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory)
            kubectl = binary / "kubectl"
            kubectl.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with pathlib.Path(os.environ["CALLS"]).open("a") as file:
    file.write(json.dumps(args) + "\\n")
pod = {"metadata": {"name": "fault-worker"},
       "spec": {"hostAliases": [{"ip": "192.0.2.1", "hostnames": ["synthetic-bus"]}]},
       "status": {"containerStatuses": []}}
if args[:2] == ["get", "pods"]:
    print(json.dumps({"items": [pod]}))
elif args[:2] == ["get", "pod"]:
    print(json.dumps(pod))
elif args[0] == "logs":
    print("Injected Service Bus timeout")
    sys.exit(int(os.environ["DIAGNOSTIC_STATUS"]))
""")
            kubectl.chmod(0o755)
            for command in ("sleep", "dig"):
                path = binary / command
                path.write_text("#!/usr/bin/env bash\nexit 0\n")
                path.chmod(0o755)
            calls_path = binary / "calls.jsonl"
            for status in (0, 29):
                with self.subTest(diagnostic_status=status):
                    calls_path.write_text("")
                    env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}",
                               CALLS=str(calls_path), DIAGNOSTIC_STATUS=str(status))
                    script = 'set -euo pipefail\nlab_value() { printf "synthetic"; }\n' + block
                    result = subprocess.run(["bash", "-c", script], env=env,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, status, result.stderr)
                    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
                    recovery = calls[-2]
                    self.assertEqual(recovery[:3], ["patch", "deployment", "order-worker"])
                    patch = json.loads(recovery[recovery.index("-p") + 1])
                    self.assertIsNone(patch["spec"]["template"]["spec"]["hostAliases"])
                    self.assertEqual(calls[-1][:3], ["rollout", "status", "deployment/order-worker"])

    def test_team_member_setup_requires_no_deployment_reads(self):
        block = self.bash_block("labs/07-governance.md", 'settings=$(jq -e .')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "local.settings.json").write_text(json.dumps({
                "TenantId": "synthetic-tenant", "SubscriptionId": "synthetic-subscription",
                "ResourceGroup": "synthetic-rg", "Prefix": "synthetic",
            }))
            az = root / "az"
            az.write_text("""#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
if args[:1] == ["deployment"]:
    sys.exit("Team identities cannot read deployments")
with pathlib.Path("calls.jsonl").open("a") as file:
    file.write(json.dumps(args) + "\\n")
""")
            az.chmod(0o755)
            for name in ("kubectl", "kubelogin"):
                path = root / name
                path.write_text("#!/usr/bin/env bash\nprintf 'yes\\n'\n")
                path.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}")
            result = subprocess.run(["bash", "-c", block], cwd=root, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            self.assertEqual([call[:2] for call in calls],
                             [["login", "--tenant"], ["account", "set"], ["aks", "get-credentials"]])
            self.assertIn("synthetic-aks", calls[-1])

    def test_upgrade_report_labels_actual_sample_population(self):
        block = self.bash_block("labs/09-upgrades.md", "ChangeStart:$changeStart")
        samples = [
            {"utc": "2026-09-24T08:01:00.000001+00:00", "status": 200},
            {"utc": "2026-09-24T08:02:00.000001+00:00", "status": 503},
            {"utc": "2026-09-24T08:03:00.000001+00:00", "status": 200},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            artifacts = root / ".artifacts/advanced"
            artifacts.mkdir(parents=True)
            (artifacts / "upgrade-traffic.json").write_text(json.dumps(samples))
            (artifacts / "upgrade-start.txt").write_text("2026-09-24T08:02:00Z\n")
            date = root / "date"
            date.write_text("#!/usr/bin/env bash\nprintf '2026-09-24T08:05:00Z\\n'\n")
            date.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}")
            result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block],
                                    cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report, _ = json.JSONDecoder().raw_decode(result.stdout)
            self.assertEqual(report["Samples"], 3)
            self.assertEqual(report["Failed"], 1)
            self.assertEqual(report["AvailabilityPercent"], 66.667)
            self.assertEqual(report["Start"], samples[0]["utc"])
            self.assertEqual(report["End"], samples[-1]["utc"])
            self.assertEqual(report["ChangeStart"], "2026-09-24T08:02:00Z")
            self.assertEqual(report["ChangeEnd"], "2026-09-24T08:05:00Z")

    def test_script_references_exist_and_are_lowercase(self):
        for guide in GUIDES:
            for path in re.findall(r"(?:scripts|ops|advanced)/[A-Za-z0-9_-]+\.sh\b", guide.read_text()):
                with self.subTest(guide=guide.relative_to(ROOT), script=path):
                    self.assertEqual(path, path.lower())
                    self.assertTrue((ROOT / path).is_file(), f"Missing helper: {path}")
        for directory in ("scripts", "ops", "advanced"):
            for path in (ROOT / directory).glob("*.sh"):
                self.assertEqual(path.name, path.name.lower())
            self.assertEqual(list((ROOT / directory).glob("*.ps1")), [])

    def test_guides_and_workflow_have_no_powershell_commands(self):
        for path in [*GUIDES, ROOT / ".github/workflows/publish-image.yml"]:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(path.read_text(), r"(?i)```powershell|\.ps1\b|\bpwsh\b|\$env:")

    def test_scripts_and_embedded_python_parse_without_execution(self):
        for directory in ("scripts", "ops", "advanced"):
            for path in (ROOT / directory).glob("*.sh"):
                with self.subTest(script=path.relative_to(ROOT)):
                    for shell in ("bash", "zsh"):
                        if shutil.which(shell):
                            result = subprocess.run([shell, "-n", str(path)], text=True, capture_output=True)
                            self.assertEqual(result.returncode, 0, result.stderr)
                    for block in re.findall(r"<<'PY'\n(.*?)\nPY", path.read_text(), re.S):
                        compile(block, str(path), "exec")

    def test_workflow_bash_steps_parse_without_execution(self):
        workflow = (ROOT / ".github/workflows/publish-image.yml").read_text()
        blocks = re.findall(r"^        run: \|\n((?:^          .*\n|^\n)*)", workflow, re.M)
        self.assertGreaterEqual(len(blocks), 2)
        for block in blocks:
            script = "\n".join(line[10:] for line in block.splitlines())
            result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
