#!/usr/bin/env python3
"""Acquire an in-memory Entra user token and probe only a loopback test tunnel."""

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

import msal


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("Redirect refused")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--client-id", required=True, type=uuid.UUID)
    parser.add_argument("--api-client-id", required=True, type=uuid.UUID)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--write", action="store_true",
                        help="Explicitly enqueue one synthetic order using a separate writer client")
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.base_url)
    if (
        url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != 18080
        or url.username or url.password or url.path not in ("", "/")
        or url.query or url.fragment
    ):
        parser.error("--base-url must be http://127.0.0.1:18080 (orders-test port-forward)")
    base = "http://127.0.0.1:18080"
    scopes = [f"api://{args.api_client_id}/orders.read"]
    if args.write:
        scopes.append(f"api://{args.api_client_id}/orders.write")
    client = msal.PublicClientApplication(
        str(args.client_id),
        authority=f"https://login.microsoftonline.com/{args.tenant_id}",
        token_cache=msal.TokenCache(),
        enable_pii_log=False,
    )
    flow = client.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError("Device sign-in unavailable; check tenant policy and client registration")
    print(flow["message"])
    result = client.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError("Sign-in failed; inspect the tenant sign-in logs, not token contents")
    token = result["access_token"]
    # No proxy, redirects, persistent token cache, token CLI argument or token output.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    order_id = f"auth-{uuid.uuid4()}"
    payload = json.dumps({"id": order_id, "item": "synthetic-auth-workshop"}).encode()
    checks = [
        ("GET", "/", None, None, 401),
        ("GET", "/", token, None, 200),
        ("POST", "/orders", token, payload, 202 if args.write else 403),
        ("GET", "/healthz", None, None, 200),
        ("GET", "/readyz", None, None, 200),
        ("GET", "/docs", None, None, 404),
        ("GET", "/openapi.json", None, None, 404),
    ]
    for method, path, bearer, data, expected in checks:
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with opener.open(req, timeout=15) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        print(f"{method} {path}: {status} (expected {expected})")
        if status != expected:
            raise RuntimeError("Unexpected status; stop and check configuration without recording tokens")
    if args.write:
        print(f"Synthetic test-queue order accepted: {order_id}; 202 is not processing proof.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Identity/HTTP exception details must not become credential evidence.
        raise SystemExit(
            "Probe failed. Check configuration/connectivity/sign-in policy; no token output."
        ) from None
