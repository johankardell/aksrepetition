import asyncio
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.exceptions import PyJWKClientConnectionError

import app
from authentication import EntraAuthorization


TENANT = "11111111-1111-4111-8111-111111111111"
AUDIENCE = "22222222-2222-4222-8222-222222222222"
CONFIG = {
    "AUTH_ENABLED": "true",
    "AUTH_TENANT_ID": TENANT,
    "AUTH_AUDIENCE": AUDIENCE,
    "AUTH_ISSUER": f"https://login.microsoftonline.com/{TENANT}/v2.0",
    "AUTH_JWKS_URL": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
}


def request(method, path, token=None, body=None):
    """Exercise ASGI routing/dependencies without a network or test-client package."""
    async def execute():
        messages = []
        received = False
        headers = [(b"content-type", b"application/json")]
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "raw_path": path.encode(),
            "root_path": "", "query_string": b"", "headers": headers,
            "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1234),
        }

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": json.dumps(body).encode() if body else b""}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await asyncio.wait_for(app.api(scope, receive, send), timeout=5)
        return next(message["status"] for message in messages if message["type"] == "http.response.start")

    return asyncio.run(execute())


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="test-key", alg="RS256", use="sig")

    def setUp(self):
        self.auth = EntraAuthorization(CONFIG)
        self.addCleanup(patch.stopall)
        patch.object(app, "authorization", self.auth).start()
        self.fetch = patch.object(self.auth.jwks, "fetch_data", return_value={"keys": [self.jwk]}).start()
        self.bus = patch.object(app, "bus_client").start()
        self.db = patch.object(app, "database").start()

    def token(self, **changes):
        now = int(time.time())
        claims = {
            "iss": CONFIG["AUTH_ISSUER"], "aud": AUDIENCE, "tid": TENANT,
            "ver": "2.0", "sub": "synthetic-user", "iat": now, "nbf": now,
            "exp": now + 300, "scp": "orders.read",
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})

    def test_missing_and_invalid_tokens_never_enqueue(self):
        valid = self.token()
        payload = jwt.decode(valid, self.key.public_key(), algorithms=["RS256"], audience=AUDIENCE)
        bad_signature = jwt.encode(payload, self.other_key, algorithm="RS256", headers={"kid": "test-key"})
        unsigned = jwt.encode(payload, None, algorithm="none", headers={"kid": "test-key"})
        for token in (None, "not-a-token", bad_signature, unsigned):
            with self.subTest(token_kind="missing" if token is None else "invalid"):
                self.assertEqual(request("POST", "/orders", token, {"id": "auth-test", "item": "synthetic"}), 401)
        self.bus.assert_not_called()

    def test_claim_validation(self):
        for changes in (
            {"iss": "https://attacker.invalid/v2.0"},
            {"aud": "another-api"},
            {"exp": int(time.time()) - 60},
            {"nbf": int(time.time()) + 300},
            {"tid": AUDIENCE},
            {"ver": "1.0"},
            {"scp": ["orders.read"]},
            {"roles": "Orders.Read"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(request("GET", "/", self.token(**changes)), 401)

    def test_read_scope_allows_read_but_forbids_write_without_side_effect(self):
        token = self.token()
        self.assertEqual(request("GET", "/", token), 200)
        with patch.dict(os.environ, {"POSTGRES_HOST": "mock-db"}):
            self.db.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
                "auth-test", "synthetic", "2026-01-01T00:00:00Z",
            )
            self.assertEqual(request("GET", "/orders/auth-test", token), 200)
        self.assertEqual(request("POST", "/orders", token, {"id": "auth-test", "item": "synthetic"}), 403)
        self.bus.assert_not_called()

    def test_write_scope_and_application_role_enqueue_only_when_authorized(self):
        for claims in ({"scp": "orders.write"}, {"scp": "", "roles": ["Orders.Write"]}):
            self.assertEqual(
                request("POST", "/orders", self.token(**claims), {"id": "auth-test", "item": "synthetic"}),
                202,
            )
        sender = self.bus.return_value.__enter__.return_value.get_queue_sender.return_value.__enter__.return_value
        self.assertEqual(sender.send_messages.call_count, 2)
        self.assertEqual(request("GET", "/", self.token(scp="", roles=["Orders.Read"])), 200)
        self.assertEqual(request("GET", "/", self.token(scp="unrelated")), 403)
        self.assertEqual(request("GET", "/", self.token(scp="orders.write")), 403)

    def test_business_config_and_probes_have_explicit_boundaries(self):
        for path in ("/", "/orders/auth-test", "/config-version"):
            self.assertEqual(request("GET", path), 401)
        self.db.assert_not_called()
        for path in ("/healthz", "/readyz", "/metrics"):
            with patch.dict(os.environ, {"FAIL_READINESS": "false"}):
                self.assertEqual(request("GET", path), 200)
        self.fetch.assert_not_called()

    def test_provider_outage_is_not_anonymous_and_is_observed(self):
        self.fetch.side_effect = PyJWKClientConnectionError("offline")
        counter = app.requests.labels("POST", "503")
        before = counter._value.get()
        self.assertEqual(request("POST", "/orders", self.token(scp="orders.write"),
                                 {"id": "auth-test", "item": "synthetic"}), 503)
        self.bus.assert_not_called()
        self.assertEqual(counter._value.get(), before + 1)

    def test_malformed_provider_key_set_fails_closed(self):
        self.fetch.return_value = {"keys": []}
        self.assertEqual(request("GET", "/", self.token()), 503)
        self.bus.assert_not_called()

    def test_fixed_trusted_jwks_transport_fails_closed(self):
        with patch("authentication.urllib.request.build_opener") as build:
            build.return_value.open.side_effect = urllib.error.URLError("offline")
            auth = EntraAuthorization(CONFIG)
            with self.assertRaises(HTTPException) as caught:
                auth.require(f"Bearer {self.token()}", "read")
            self.assertEqual(caught.exception.status_code, 503)
            build.return_value.open.assert_called_once_with(CONFIG["AUTH_JWKS_URL"], timeout=5)

    def test_disabled_and_worker_startup_keep_existing_behavior(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(app.api_authorization())
        with patch.object(app, "authorization", None):
            self.assertEqual(request("GET", "/"), 200)
            self.assertEqual(request("POST", "/orders", body={"id": "auth-test", "item": "synthetic"}), 202)
        with patch.dict(os.environ, {"ROLE": "worker", "AUTH_ENABLED": "invalid"}, clear=True):
            self.assertIsNone(app.api_authorization())

    def test_invalid_enabled_configuration_fails_startup(self):
        for changes in (
            {"AUTH_ENABLED": "TRUE"},
            {"AUTH_TENANT_ID": "common"},
            {"AUTH_AUDIENCE": ""},
            {"AUTH_ISSUER": "https://login.microsoftonline.com/common/v2.0"},
            {"AUTH_JWKS_URL": "http://login.microsoftonline.com/keys"},
            {"AUTH_JWKS_URL": "https://attacker.invalid/keys"},
        ):
            with self.subTest(changes=changes), patch.dict(os.environ, CONFIG | changes, clear=True):
                with self.assertRaises(ValueError):
                    app.api_authorization()
        with patch.dict(os.environ, {"AUTH_ENABLED": "true"}, clear=True):
            with self.assertRaises(ValueError):
                app.api_authorization()

    def test_enabled_api_startup_removes_schema_and_explorers(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import app; paths = {route.path for route in app.api.routes}; "
             "assert not paths.intersection({'/docs', '/redoc', '/openapi.json'}); "
             "assert {'/healthz', '/readyz', '/metrics'} <= paths"],
            env={**os.environ, **CONFIG, "ROLE": "api", "APPLICATIONINSIGHTS_CONNECTION_STRING": ""},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
