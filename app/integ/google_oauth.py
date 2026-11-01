"""Google service-account OAuth2 — shared by GSC/GA4/Ads adapters.

Real RS256 JWT assertion (cryptography) -> Google token endpoint. No long-lived
refresh flows; tokens cached until near expiry. Transport is injectable so tests
verify request/header shapes without network.
"""
import base64
import json
import os
import time
from typing import Callable, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

TOKEN_URL = "https://oauth2.googleapis.com/token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

Transport = Callable[[str, dict, dict], object]   # async (url, headers, json_body) -> response-dict


class OAuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def load_service_account(env_key: str = "GOOGLE_OAUTH_JSON") -> dict:
    raw = os.environ.get(env_key) or ""
    if not raw:
        raise OAuthError(f"{env_key} not set (path or JSON of the service-account key)")
    if raw.endswith(".json") and os.path.exists(raw):
        with open(raw) as f:
            return json.load(f)
    return json.loads(raw)


def build_assertion(sa: dict, scopes: list[str], now: Optional[int] = None) -> str:
    """RS256-signed JWT bearer assertion (Google service-account flow)."""
    now = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": sa["client_email"],
        "scope": " ".join(scopes),
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(sig)


class GoogleOAuth:
    def __init__(self, transport: Optional[Transport] = None):
        self._transport: Optional[Transport] = transport
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    async def access_token(self, scopes: list[str]) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        sa = load_service_account()
        assertion = build_assertion(sa, scopes)
        body = {"grant_type": GRANT_TYPE, "assertion": assertion}
        if self._transport:
            resp = await self._transport(TOKEN_URL, {}, body)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(TOKEN_URL, data=body)
                r.raise_for_status()
                resp = r.json()
        token = resp.get("access_token")
        if not token:
            raise OAuthError(f"token exchange failed: {str(resp)[:200]}")
        self._token = token
        self._expires_at = time.time() + int(resp.get("expires_in", 3600))
        return token


async def post_json(url: str, headers: dict, body: dict,
                    transport: Optional[Transport] = None) -> dict:
    if transport:
        return await transport(url, headers, body)
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(url, headers=headers, json=body)
        r.raise_for_status()
        return r.json()
