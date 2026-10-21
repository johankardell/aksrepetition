"""Opt-in, single-tenant Entra access-token validation for the API only."""

import json
import ssl
import urllib.error
import urllib.request
import uuid

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import (
    InvalidKeyError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKError,
    PyJWKSetError,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("JWKS redirects are not allowed")


class TrustedJWKClient(PyJWKClient):
    def fetch_data(self):
        # Fixed tenant endpoint, verified TLS, no redirects, bounded time/body.
        opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )
        try:
            with opener.open(self.uri, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("JWKS response too large")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                raise ValueError("Invalid JWKS document")
        except (OSError, ValueError) as error:
            raise PyJWKClientConnectionError("JWKS unavailable") from error
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data


class EntraAuthorization:
    def __init__(self, environment):
        try:
            self.tenant = str(uuid.UUID(environment["AUTH_TENANT_ID"]))
            self.audience = str(uuid.UUID(environment["AUTH_AUDIENCE"]))
            self.issuer = environment["AUTH_ISSUER"]
            jwks_url = environment["AUTH_JWKS_URL"]
            if self.issuer != f"https://login.microsoftonline.com/{self.tenant}/v2.0":
                raise ValueError("Issuer must be the configured tenant's v2 issuer")
            if jwks_url != f"https://login.microsoftonline.com/{self.tenant}/discovery/v2.0/keys":
                raise ValueError("JWKS must be the configured tenant's HTTPS endpoint")
        except (KeyError, ValueError) as error:
            raise ValueError("Invalid enabled API authentication configuration") from error
        self.jwks = TrustedJWKClient(jwks_url, timeout=5, lifespan=300, cache_keys=False)

    def require(self, authorization, permission):
        unauthorized = HTTPException(
            401, "Valid bearer access token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
        if not authorization or len(authorization) > 16384:
            raise unauthorized
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise unauthorized
        token = parts[1]
        try:
            # Only the header is inspected before verification, never trusted claims
            # or token-supplied discovery URLs.
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise unauthorized
            key = self.jwks.get_signing_key(header["kid"])
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"],
                audience=self.audience, issuer=self.issuer,
                options={
                    "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tid", "ver"],
                    "strict_aud": True,
                },
            )
        except (PyJWKClientConnectionError, PyJWKError, PyJWKSetError, InvalidKeyError):
            raise HTTPException(503, "Token verification unavailable") from None
        except (jwt.InvalidTokenError, PyJWKClientError, ValueError, TypeError):
            raise unauthorized from None
        if claims["tid"] != self.tenant or claims["ver"] != "2.0":
            raise unauthorized
        scopes = claims.get("scp", "")
        roles = claims.get("roles", [])
        if not isinstance(scopes, str) or not isinstance(roles, list) or not all(
            isinstance(role, str) for role in roles
        ):
            raise unauthorized
        if f"orders.{permission}" not in scopes.split() and f"Orders.{permission.title()}" not in roles:
            raise HTTPException(403, "Required orders permission missing")
