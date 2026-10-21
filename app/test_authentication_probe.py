import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location(
    "authentication_probe", Path(__file__).resolve().parents[1] / "ops/authentication/probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
API = "33333333-3333-4333-8333-333333333333"
ARGS = ["probe.py", "--tenant-id", TENANT, "--client-id", CLIENT, "--api-client-id", API]


class AuthenticationProbeTests(unittest.TestCase):
    def test_in_memory_scope_and_status_contract(self):
        for write in (False, True):
            with self.subTest(write=write), \
                    patch.object(probe.msal, "PublicClientApplication") as client_type, \
                    patch.object(probe.urllib.request, "build_opener") as opener_type, \
                    patch("sys.argv", ARGS + (["--write"] if write else [])):
                client = client_type.return_value
                client.initiate_device_flow.return_value = {"user_code": "synthetic", "message": "Sign in"}
                client.acquire_token_by_device_flow.return_value = {"access_token": "secret-not-for-output"}
                statuses = iter([401, 200, 202 if write else 403, 200, 200, 404, 404])

                def respond(req, **kwargs):
                    status = next(statuses)
                    if status >= 400:
                        raise urllib.error.HTTPError(req.full_url, status, "expected", {}, None)
                    response = MagicMock()
                    response.__enter__.return_value.status = status
                    return response

                opener_type.return_value.open.side_effect = respond
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    probe.main()
                scopes = [f"api://{API}/orders.read"]
                if write:
                    scopes.append(f"api://{API}/orders.write")
                client.initiate_device_flow.assert_called_once_with(scopes=scopes)
                self.assertNotIn("secret-not-for-output", output.getvalue())
                self.assertIn("POST /orders: 202" if write else "POST /orders: 403", output.getvalue())
                calls = opener_type.return_value.open.call_args_list
                self.assertIsNone(calls[0].args[0].get_header("Authorization"))
                self.assertEqual(calls[1].args[0].get_header("Authorization"), "Bearer secret-not-for-output")
                self.assertTrue(all(call.args[0].full_url.startswith("http://127.0.0.1:18080/") for call in calls))

    def test_refuses_remote_targets_and_redirects(self):
        with patch("sys.argv", ARGS + ["--base-url", "http://untrusted.invalid:18080"]), \
                patch.object(probe.msal, "PublicClientApplication") as client, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                probe.main()
            client.assert_not_called()
        with self.assertRaises(urllib.error.URLError):
            probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid")


if __name__ == "__main__":
    unittest.main()
