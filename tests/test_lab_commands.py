import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import unquote, urlsplit


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDES = [
    ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / ".github/copilot-instructions.md",
    *sorted((ROOT / "labs").glob("*.md")),
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "workshops").rglob("*.md")),
]


class LabCommandsTests(unittest.TestCase):
    def test_curriculum_inventory_and_navigation(self):
        guides = sorted((ROOT / "labs").glob("*.md"))
        expected = [*range(1, 7), *range(10, 16), *range(20, 30)]
        self.assertEqual([int(guide.name[:2]) for guide in guides], expected)
        readme = (ROOT / "README.md").read_text()
        for guide in guides:
            with self.subTest(guide=guide.name):
                number = int(guide.name[:2])
                self.assertRegex(guide.read_text().splitlines()[0], rf"^# Lab {number} [—-] ")
                self.assertIn(f"](labs/{guide.name})", readme)

    def test_local_guide_links_and_fragments_resolve(self):
        for guide in GUIDES:
            for target in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", guide.read_text()):
                url = urlsplit(target)
                if url.scheme or url.netloc or (url.path and not url.path.endswith(".md")):
                    continue
                path = (guide.parent / unquote(url.path)).resolve() if url.path else guide
                with self.subTest(guide=guide.name, target=target):
                    self.assertTrue(path.is_file(), f"Missing guide: {path}")
                    if url.fragment:
                        headings = re.findall(r"^#{1,6} (.+)$", path.read_text(), re.M)
                        anchors = [re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                                   for heading in headings]
                        self.assertIn(unquote(url.fragment), anchors)

    def test_lab_contracts_are_visible_and_specific(self):
        for guide in sorted((ROOT / "labs").glob("*.md")):
            text = guide.read_text()
            with self.subTest(guide=guide.name):
                self.assertIn("## Lab contract", text)
                contract = text.split("## Lab contract", 1)[1].split("\n## ", 1)[0]
                self.assertNotIn("<details>", contract)
                for field in ("Entry", "Host and identity", "Time", "Cost", "Changes", "Exit and re-entry"):
                    self.assertRegex(contract, rf"\| \*?\*?{re.escape(field)}\*?\*? \| .+\|")
                self.assertIn("../docs/state.md", text)

    def test_every_lab_ends_with_next_lab_navigation(self):
        guides = sorted((ROOT / "labs").glob("*.md"))
        for index, guide in enumerate(guides):
            with self.subTest(guide=guide.name):
                text = guide.read_text().rstrip()
                footer = text.splitlines()[-1]
                self.assertGreater(text.rfind(footer), text.rfind("</details>"))
                if index + 1 < len(guides):
                    next_guide = guides[index + 1]
                    number = int(next_guide.name[:2])
                    prefix = "**Next lab (optional):**" if number >= 24 else "**Next lab:**"
                    self.assertTrue(footer.startswith(f"{prefix} [Lab {number} - "))
                    targets = re.findall(r"\]\(([^)]+)\)", footer)
                    self.assertEqual([urlsplit(target).path for target in targets], [next_guide.name])
                else:
                    self.assertTrue(footer.startswith("**Course complete:**"))
                    self.assertIn("](../README.md)", footer)

    def test_foundation_includes_bastion_module_and_outputs(self):
        foundation = (ROOT / "infra/main.bicep").read_text()
        module = re.search(r"module bastion 'bastion[.]bicep' = \{.*?\n\}", foundation, re.S)
        self.assertIsNotNone(module)
        self.assertIn("name: 'bastion'", module[0])
        for parameter in ("prefix: prefix", "location: location", "vnetName: vnet.name"):
            self.assertIn(parameter, module[0])
        for output, value in (("bastionId", "bastionId"), ("bastionName", "bastionName"),
                              ("bastionPublicIpId", "publicIpId")):
            self.assertIn(f"output {output} string = bastion.outputs.{value}", foundation)
        connector = (ROOT / "scripts/connect-aks-bastion.sh").read_text()
        self.assertIn("-n bastion --query properties.outputs.bastionId.value", connector)

    def test_service_bus_workload_roles_use_queue_scoped_builtin_definitions(self):
        sender = "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"
        receiver = "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"
        workload = (ROOT / "infra/workload-identities.bicep").read_text()
        for resource, role, identity in (("send", sender, "api"), ("receive", receiver, "worker")):
            assignment = re.search(
                rf"resource {resource} .*?(?=\nresource |\noutput )", workload, re.S)
            self.assertIsNotNone(assignment)
            self.assertIn(f"'{role}'", assignment[0])
            self.assertIn("scope: queue", assignment[0])
            self.assertIn(f"principalId: {identity}.properties.principalId", assignment[0])
        delivery = (ROOT / "ops/delivery.bicep").read_text()
        roles = re.search(r"var dataRoles = \[(.*?)\]", delivery, re.S)
        self.assertIsNotNone(roles)
        self.assertEqual(re.findall(r"'([^']+)'", roles[1]), [sender, receiver])
        self.assertIn("scope: queue", delivery)

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

    def test_offline_lifecycle_rehearsal_detects_replayed_network_state(self):
        guide = "workshops/infrastructure-lifecycle.md"
        script = self.bash_block(guide, "cat > .artifacts/lifecycle/bootstrap.json")
        script += "\n" + self.bash_block(guide, "cat > .artifacts/lifecycle/desired.json")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["bash", "-c", script], cwd=directory,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('-  "outboundType": "loadBalancer"', result.stdout)
            self.assertIn('+  "outboundType": "userDefinedRouting"', result.stdout)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_offline_artifact_rehearsal_accepts_and_rejects_evidence(self):
        guide = "workshops/artifact-trust.md"
        script = "\n".join(self.bash_block(guide, marker) for marker in (
            "openssl genpkey", "from datetime import datetime", "sbom.saved",
            "rm -- .artifacts/trust/signer.key",
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "ops").symlink_to(ROOT / "ops", target_is_directory=True)
            (root / "tests").symlink_to(ROOT / "tests", target_is_directory=True)
            result = subprocess.run(["bash", "-c", script], cwd=directory,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"allowed": true', result.stdout)
            self.assertFalse((root / ".artifacts/trust/signer.key").exists())

    def test_cumulative_guides_preserve_active_bastion_kubeconfigs(self):
        cumulative_numbers = {*range(1, 7), *range(10, 16), *range(20, 25), 28}
        for guide in sorted((ROOT / "labs").glob("*.md")):
            if int(guide.name[:2]) not in cumulative_numbers:
                continue
            for block in re.findall(r"^```bash\n(.*?)^```", guide.read_text(), re.M | re.S):
                with self.subTest(guide=guide.name):
                    self.assertNotIn("az aks get-credentials", block)

    def test_bastion_tls_evidence_rejects_unverified_or_wrong_endpoints(self):
        for guide, port in (("labs/03-access-and-identity.md", 8443),
                            ("labs/10-baseline.md", 8443), ("labs/23-multi-region.md", 8444)):
            block = self.bash_block(guide, "TunnelConfig=$(kubectl config view")
            check = re.search(r'jq -e --arg fqdn .*?<<< "\$TunnelConfig"', block, re.S)[0]
            endpoint = {
                "server": f"https://localhost:{port}/", "tls-server-name": "test.private.azmk8s.io",
                "certificate-authority-data": "synthetic-ca",
            }
            variants = [
                (endpoint, "kubelogin", True),
                ({**endpoint, "insecure-skip-tls-verify": True}, "kubelogin", False),
                ({**endpoint, "tls-server-name": "localhost"}, "kubelogin", False),
                ({**endpoint, "certificate-authority-data": ""}, "kubelogin", False),
                ({**endpoint, "server": "https://public.example/"}, "kubelogin", False),
                (endpoint, "other-authentication", False),
            ]
            for cluster, authentication, valid in variants:
                with self.subTest(guide=guide, cluster=cluster, authentication=authentication):
                    config = {"clusters": [{"cluster": cluster}],
                              "users": [{"user": {"exec": {"command": authentication}}}]}
                    script = 'fqdn=test.private.azmk8s.io\nTunnelConfig=$(cat)\n' + check
                    result = subprocess.run(["bash", "-c", script], input=json.dumps(config),
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_beginner_render_guard_preserves_existing_state(self):
        block = self.bash_block("labs/05-acr-and-workloads.md", "if [[ -e ./rendered/base")
        guard = block.split("bash ./scripts/render-manifests.sh", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory) / "rendered/base"
            for existing in (False, True):
                with self.subTest(existing=existing):
                    if existing:
                        base.mkdir(parents=True)
                        (base / "identity-patch.yaml").write_text("preserve me\n")
                    result = subprocess.run(["bash", "-c", guard], cwd=directory,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode == 0, not existing)
                    if existing:
                        self.assertIn("do not rerender", result.stderr)
                        self.assertEqual((base / "identity-patch.yaml").read_text(), "preserve me\n")

    def test_beginner_fault_rejects_wrong_evidence_and_always_attempts_recovery(self):
        block = self.bash_block("labs/06-troubleshooting-and-lifecycle.md", "restore_selector()")
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory)
            kubectl = binary / "kubectl"
            kubectl.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
mode = os.environ["MODE"]
with pathlib.Path(os.environ["CALLS"]).open("a") as file:
    file.write(json.dumps(args) + "\\n")
if args[0] == "patch":
    selector = json.loads(args[args.index("-p") + 1])["spec"]["template"]["spec"]["nodeSelector"]["agentpool"]
    if selector == "apps" and mode == "recovery-failure":
        sys.exit(19)
elif args[:2] == ["rollout", "status"] and "--timeout=30s" in args:
    if mode == "unexpected-success":
        sys.exit(0)
    print("connection refused" if mode == "wrong-error" else "error: timed out waiting for the condition",
          file=sys.stderr)
    sys.exit(1)
elif args[:2] == ["get", "pods"]:
    print(json.dumps({"items": [{
        "spec": {"nodeSelector": {"agentpool": "missing"}},
        "status": {"conditions": [{
            "type": "PodScheduled", "status": "False", "reason": "Unschedulable",
            "message": "Insufficient cpu" if mode == "wrong-scheduler" else
                       "0/6 nodes are available: didn't match Pod's node affinity/selector"
        }]}
    }]}))
elif args[:2] == ["get", "events"] and mode == "diagnostic-failure":
    sys.exit(25)
""")
            kubectl.chmod(0o755)
            calls_path = binary / "calls.jsonl"
            for mode in ("expected", "unexpected-success", "wrong-error", "wrong-scheduler",
                         "diagnostic-failure", "recovery-failure"):
                with self.subTest(mode=mode):
                    calls_path.write_text("")
                    env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}",
                               MODE=mode, CALLS=str(calls_path))
                    result = subprocess.run(["bash", "-c", block], env=env,
                                            text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode == 0, mode == "expected", result.stderr)
                    if mode == "diagnostic-failure":
                        self.assertEqual(result.returncode, 25)
                    if mode == "recovery-failure":
                        self.assertIn("Recovery failed", result.stderr)
                    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
                    patches = [call for call in calls if call[0] == "patch"]
                    selectors = [json.loads(call[call.index("-p") + 1])["spec"]["template"]
                                 ["spec"]["nodeSelector"]["agentpool"] for call in patches]
                    self.assertEqual(selectors, ["missing", "apps"])
                    if mode != "recovery-failure":
                        self.assertEqual(calls[-1][:3], ["rollout", "status", "deployment/order-api"])
                        self.assertIn("--timeout=300s", calls[-1])

    def test_beginner_handoff_rejects_unhealthy_or_stale_rollouts(self):
        block = self.bash_block("labs/06-troubleshooting-and-lifecycle.md",
                               "test -s ./rendered/base/kustomization.yaml")
        self.assertNotRegex(block, r"deploy-foundation|render-manifests|kubectl (apply|patch|delete)")
        node_check = re.search(r"kubectl get nodes -o json \| jq -e '(.*?)'", block, re.S)[1]
        deployment_check = re.search(r"kubectl get deployments -n orders -o json \| jq -e '(.*?)'",
                                     block, re.S)[1]
        nodes = {"items": [
            {"metadata": {"labels": {"agentpool": pool}},
             "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
            for pool in ("system", "system", "system", "apps", "apps", "apps")
        ]}
        deployments = {"items": [
            {"metadata": {"name": name, "generation": 3},
             "spec": {"replicas": count, "template": {"spec": {"nodeSelector": {"agentpool": "apps"}}}},
             "status": {"observedGeneration": 3, "updatedReplicas": count, "availableReplicas": count}}
            for name, count in (("order-api", 2), ("order-worker", 1))
        ]}
        for problem in ("none", "missing-node", "not-ready", "wrong-pool"):
            data = json.loads(json.dumps(nodes))
            if problem == "missing-node":
                data["items"].pop()
            elif problem == "not-ready":
                data["items"][0]["status"]["conditions"][0]["status"] = "False"
            elif problem == "wrong-pool":
                data["items"][0]["metadata"]["labels"]["agentpool"] = "apps"
            with self.subTest(nodes=problem):
                result = subprocess.run(["jq", "-e", node_check], input=json.dumps(data),
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, problem == "none")
        for problem in ("none", "missing-worker", "stale-generation", "old-replicas", "unavailable",
                        "fault-selector", "changed-count"):
            data = json.loads(json.dumps(deployments))
            api = data["items"][0]
            if problem == "missing-worker":
                data["items"].pop()
            elif problem == "stale-generation":
                api["status"]["observedGeneration"] = 2
            elif problem == "old-replicas":
                api["status"]["updatedReplicas"] = 1
            elif problem == "unavailable":
                api["status"]["availableReplicas"] = 1
            elif problem == "fault-selector":
                api["spec"]["template"]["spec"]["nodeSelector"]["agentpool"] = "missing"
            elif problem == "changed-count":
                api["spec"]["replicas"] = 3
            with self.subTest(deployments=problem):
                result = subprocess.run(["jq", "-e", deployment_check], input=json.dumps(data),
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, problem == "none")

    def test_expected_failures_preserve_strict_session_and_reject_wrong_evidence(self):
        examples = [
            ("labs/11-identity.md", 'Denial=$(kubectl exec -n orders "$workerPod"',
             "azure.core.exceptions.HttpResponseError: (Forbidden) Status: 403"),
            ("labs/12-networking.md", "'http://order-api/readyz'",
             "urllib.error.URLError: <urlopen error timed out>"),
            ("labs/12-networking.md", "'https://example.org'",
             "ConnectionResetError: Connection reset by peer"),
            ("labs/13-gitops.md", "Reconciliation=$(flux reconcile",
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
        block = self.bash_block("labs/12-networking.md", 'worker=\'\'')
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
        block = self.bash_block("labs/20-governance.md", 'settings=$(jq -e .')
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
                                    input="/subscriptions/synthetic/resourceGroups/synthetic-rg/providers/Microsoft.Network/bastionHosts/synthetic-bastion\n",
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            self.assertEqual([call[:2] for call in calls],
                             [["login", "--tenant"], ["account", "set"], ["aks", "bastion"]])
            self.assertIn("synthetic-aks", calls[-1])
            self.assertNotIn("--admin", calls[-1])

    def test_upgrade_report_labels_actual_sample_population(self):
        block = self.bash_block("labs/22-upgrades.md", "ChangeStart:$changeStart")
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
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = path.read_text()
            blocks = re.findall(r"^        run: \|\n((?:^          .*\n|^\n)*)", workflow, re.M)
            self.assertGreaterEqual(len(blocks), 1, str(path))
            for block in blocks:
                script = "\n".join(line[10:] for line in block.splitlines())
                result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")

    def test_pr_validation_is_separate_from_private_publishing(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        for forbidden in ("pull_request_target", "self-hosted", "id-token:", "azure/login@", "${{ secrets."):
            self.assertNotIn(forbidden, validation)
        self.assertIn("pull_request:", validation)
        self.assertIn("contents: read", validation)
        self.assertIn("python -m unittest discover -s tests -v", validation)
        publishing = (ROOT / ".github/workflows/publish-image.yml").read_text()
        self.assertIn("runs-on: [self-hosted, Linux, X64, aks-private]", publishing)
        self.assertNotIn("pull_request:", publishing)


if __name__ == "__main__":
    unittest.main()
