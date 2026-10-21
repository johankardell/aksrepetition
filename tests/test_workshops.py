"""Offline reliability fixtures and diagnostic behavior; never contacts Azure or AKS."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "workshops" / "assets" / "reliability"


def load(name):
    spec = importlib.util.spec_from_file_location(f"workshop_{name}", ASSETS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostic = load("diagnostic")
memory = load("memory")
traffic = load("traffic")
renderer = load("render")


class ProjectFiles(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / ".artifacts" / "workshop-tests" / uuid.uuid4().hex
        self.work.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.work)
        try:
            self.work.parent.rmdir()
        except OSError:
            pass


class DiagnosticTests(ProjectFiles):
    def test_startup_readiness_and_liveness_have_distinct_effects(self):
        now = [100.0]
        state = diagnostic.State(self.work, clock=lambda: now[0])
        self.assertEqual(state.status("/startupz"), 503)
        self.assertEqual(state.status("/readyz"), 503)
        self.assertEqual(state.status("/livez"), 200)
        now[0] += 12
        self.assertEqual(state.status("/startupz"), 200)
        self.assertEqual(state.status("/readyz"), 200)
        state.fault("ready", 20)
        self.assertEqual(state.status("/readyz"), 503)
        self.assertEqual(state.status("/livez"), 200)
        self.assertEqual(state.status("/startupz"), 200)
        now[0] += 20
        self.assertEqual(state.status("/readyz"), 200)
        state.fault("live", 20)
        self.assertEqual(state.status("/livez"), 503)
        self.assertEqual(state.status("/readyz"), 200)
        restarted = diagnostic.State(self.work, clock=lambda: now[0])
        self.assertEqual(restarted.status("/startupz"), 503)
        self.assertEqual(restarted.status("/livez"), 503)
        now[0] += 20
        self.assertEqual(restarted.status("/startupz"), 200)
        self.assertEqual(restarted.status("/livez"), 200)

    def test_faults_reject_unbounded_durations_without_writing(self):
        state = diagnostic.State(self.work)
        for probe, seconds in (("ready", 0), ("live", 31), ("startup", 10)):
            with self.subTest(probe=probe, seconds=seconds), self.assertRaises(ValueError):
                state.fault(probe, seconds)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_drain_preserves_inflight_request_but_rejects_new_work(self):
        state = diagnostic.State(self.work, startup_seconds=0)
        entered = threading.Event()
        finish = threading.Event()

        def in_flight(_seconds):
            entered.set()
            if not finish.wait(3):
                raise RuntimeError("Test did not release in-flight request")

        server = diagnostic.ThreadingHTTPServer(
            ("127.0.0.1", 0), diagnostic.handler_for(state, "v-test", sleep=in_flight),
        )
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"

        def request():
            with urllib.request.urlopen(url + "/work", timeout=4) as response:
                return response.status, json.load(response)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(request)
                try:
                    self.assertTrue(entered.wait(2))
                    (self.work / "drain").write_text("draining")
                    self.assertEqual(state.status("/readyz"), 503)
                    self.assertEqual(state.status("/livez"), 200)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(url + "/work", timeout=2)
                    self.assertEqual(error.exception.code, 503)
                    error.exception.close()
                finally:
                    finish.set()
                status, body = pending.result(timeout=3)
                self.assertEqual(status, 200)
                self.assertEqual(body["revision"], "v-test")
        finally:
            finish.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())


class BoundedProbeTests(unittest.TestCase):
    def test_memory_guard_rejects_unknown_or_unlimited_cgroups(self):
        for exists, value in ((False, ""), (True, "max")):
            path = Mock()
            path.exists.return_value = exists
            path.read_text.return_value = value
            allocate = Mock()
            with patch.object(memory, "Path", return_value=path), self.assertRaises(RuntimeError):
                memory.run(allocate=allocate)
            allocate.assert_not_called()

    def test_memory_probe_refuses_missing_or_large_container_limits(self):
        allocate = Mock()
        for limit in (0, -1, 65 * memory.MIB, 2**63):
            with self.subTest(limit=limit), self.assertRaises(RuntimeError):
                memory.run(limit=limit, allocate=allocate)
        allocate.assert_not_called()

    def test_memory_probe_has_allocation_and_time_caps_even_if_not_killed(self):
        block = memory.touched_chunk(8193)
        self.assertEqual([block[index] for index in (0, 4096, 8192)], [ord("x")] * 3)
        allocate = Mock(return_value=None)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "survived"):
            memory.run(limit=64 * memory.MIB, allocate=allocate,
                       sleep=lambda _: None, clock=lambda: 0)
        self.assertEqual(allocate.call_count, 96)
        allocate.assert_called_with(memory.MIB)
        allocate.reset_mock()
        clock = iter((0, 0, 9))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "survived"):
            memory.run(limit=64 * memory.MIB, allocate=allocate,
                       sleep=lambda _: None, clock=lambda: next(clock))
        self.assertEqual(allocate.call_count, 1)

    def test_traffic_counts_http_network_and_payload_failures(self):
        response = io.BytesIO(b'{"pod":"pod-one","revision":"v1"}')
        response.status = 200
        result = traffic.sample("http://unused", opener=Mock(return_value=response))
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["revision"], "v1")
        for exception, expected in (
            (urllib.error.HTTPError("http://unused", 503, "draining", {}, None), 503),
            (TimeoutError("timeout"), None),
            (urllib.error.URLError("DNS failure"), None),
        ):
            with self.subTest(exception=type(exception).__name__):
                result = traffic.sample("http://unused", opener=Mock(side_effect=exception))
                self.assertEqual(result["status"], expected)
        for payload in (b"not JSON", b"[]"):
            response = io.BytesIO(payload)
            response.status = 200
            result = traffic.sample("http://unused", opener=Mock(return_value=response))
            self.assertIsNone(result["status"])
            self.assertIn("error", result)

    def test_traffic_stops_at_deadline_and_returns_failure_for_any_failed_sample(self):
        for statuses, expected in (([200, 200], 0), ([200, 503], 1)):
            now = [0.0]
            samples = iter(statuses)

            def sleep(seconds):
                now[0] += seconds

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = traffic.run(
                    seconds=1, take_sample=lambda _: {"status": next(samples)},
                    clock=lambda: now[0], sleep=sleep,
                )
            self.assertEqual(result, expected)
            summary = json.loads(output.getvalue().splitlines()[-1])["summary"]
            self.assertEqual(summary["requests"], 2)
            self.assertEqual(summary["failed"], expected)
        for seconds in (0, 121):
            with self.assertRaises(ValueError):
                traffic.run(seconds=seconds)


class RenderTests(ProjectFiles):
    def test_render_rejects_mutable_external_or_wrong_repository_images(self):
        registry = "approved.azurecr.io"
        digest = "a" * 64
        for image in (
            f"{registry}/order-app:latest",
            f"other.azurecr.io/order-app@sha256:{digest}",
            f"{registry}/other-app@sha256:{digest}",
            f"{registry}/order-app@sha256:short",
            f"{registry}/order-app@sha256:{digest}\nextra: injected",
        ):
            with self.subTest(image=image), self.assertRaises(ValueError):
                renderer.render(registry, image, self.work / "rendered")
        self.assertFalse((self.work / "rendered").exists())

    def test_render_does_not_overwrite_existing_state(self):
        sentinel = self.work / "sentinel"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            renderer.render("approved.azurecr.io",
                            "approved.azurecr.io/order-app@sha256:" + "a" * 64, self.work)
        self.assertEqual(sentinel.read_text(), "keep")

    @unittest.skipUnless(shutil.which("kubectl"), "kubectl required for offline Kustomize validation")
    def test_actual_render_builds_base_and_opt_in_jobs_with_only_approved_image(self):
        image = "approved.azurecr.io/order-app@sha256:" + "b" * 64
        destination = self.work / "rendered"
        output = renderer.render("approved.azurecr.io", image, destination)
        self.assertEqual(output, (destination / "bundle.yaml").read_text())
        self.assertIn("kind: Deployment", output)
        self.assertIn("diagnostic.py: |", output)
        self.assertIn(image, output)
        self.assertIn((destination / "run-id").read_text().strip(), output)
        self.assertNotIn("__WORKSHOP_", output)
        self.assertNotIn("kind: Job\n", output)
        self.assertEqual(set(re.findall(r"^  namespace: (.+)$", output, re.M)),
                         {"workload-reliability"})
        # Validate the opt-in Jobs too, without creating them or contacting a cluster.
        kustomization = destination / "kustomization.yaml"
        kustomization.write_text(kustomization.read_text().replace(
            "  - namespace.yaml",
            "  - namespace.yaml\n  - memory-job.yaml\n  - traffic-job.yaml",
        ))
        result = subprocess.run(["kubectl", "kustomize", str(destination)],
                                capture_output=True, text=True, check=True, timeout=30)
        self.assertEqual(result.stdout.count("kind: Job\n"), 2)
        self.assertEqual(re.findall(r"^\s+image: (.+)$", result.stdout, re.M), [image] * 3)
        self.assertNotIn("__WORKSHOP_", result.stdout)

    def test_learner_command_blocks_parse_without_running_them(self):
        for guide in ("workload-reliability.md", "incident-response.md"):
            contents = (ROOT / "workshops" / guide).read_text()
            for index, block in enumerate(re.findall(r"^```bash\n(.*?)^```", contents, re.M | re.S)):
                with self.subTest(guide=guide, block=index):
                    result = subprocess.run(["bash", "-n"], input=block, text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
