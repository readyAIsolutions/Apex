"""Unit tests (fast, no DB): hashes, clustering, mock Odoo adapter, router routing,
Google service-account OAuth, GSC request shape, dev-demand honesty."""
import asyncio
import json

import pytest

from app.integ.google_oauth import build_assertion
from app.integ.odoo_crm import OdooCRMAdapter
from app.services.approvals import canonical_hash
from worker.handlers import cluster_by_intent, classify_intent


def test_canonical_hash_stable_across_key_order():
    a = canonical_hash({"b": 2, "a": 1, "nested": {"x": [1, 2]}})
    b = canonical_hash({"nested": {"x": [1, 2]}, "a": 1, "b": 2})
    assert a == b
    c = canonical_hash({"b": 2, "a": 1, "nested": {"x": [1, 3]}})
    assert a != c


def test_classify_intent():
    assert classify_intent("emergency flood cleanup now") == "emergency"
    assert classify_intent("how much does it cost") == "price"
    assert classify_intent("are you licensed and insured") == "trust"
    assert classify_intent("can you book a walkthrough") == "consultation"
    assert classify_intent("what is mold remediation") == "informational"


def test_cluster_dedupes():
    clusters = cluster_by_intent(["FLOOD damage emergency", "flood damage emergency",
                                  "cost of AI receptionist"])
    by_intent = {c["intent"]: c for c in clusters}
    assert by_intent["emergency"]["count"] == 1
    assert by_intent["price"]["count"] == 1


def test_mock_odoo_lifecycle():
    adapter = OdooCRMAdapter(mock=True)
    assert asyncio.run(adapter.health_check()).ok

    r = asyncio.run(adapter.execute("create_lead", {"full_name": "Acme", "external_ref": "ref-1"}))
    assert r.ok and r.data["id"]
    rec = asyncio.run(adapter.reconcile("ref-1"))
    assert rec.found and rec.external_id == str(r.data["id"])

    u = asyncio.run(adapter.execute("update_stage", {"id": r.data["id"], "stage": "qualified"}))
    assert u.ok and u.data["stage"] == "qualified"


def test_mock_odoo_unknown_op_fails_closed():
    adapter = OdooCRMAdapter(mock=True)
    r = asyncio.run(adapter.execute("nonsense", {}))
    assert r.ok is False and any("unknown op" in w for w in r.warnings)


def test_router_env_override(monkeypatch):
    from app.integ import Router
    from app.integ.base import ShellAdapter
    reg = {"ga4": ShellAdapter("ga4", []), "gsc": ShellAdapter("gsc", [])}
    router = Router(reg)
    monkeypatch.setenv("APEX_ROUTE_ANALYTICS", "ga4")
    adapter = asyncio.run(router.route("analytics"))
    assert adapter.name == "ga4"


# ----------------------------------------------------------------------------
# Google service-account OAuth + GSC request shape (no network: fake transport)
# ----------------------------------------------------------------------------
def _make_service_account():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return {"client_email": "apex-sa@test.iam.gserviceaccount.com",
            "private_key": pem}, key


def test_build_assertion_rs256():
    sa, pubkey = _make_service_account()
    token = build_assertion(sa, ["https://www.googleapis.com/auth/webmasters.readonly"], now=1_700_000_000)
    header_b64, claims_b64, sig_b64 = token.split(".")
    header, claims = json.loads(_b64url_decode(header_b64)), json.loads(_b64url_decode(claims_b64))
    assert header["alg"] == "RS256"
    assert claims["iss"] == sa["client_email"]
    assert claims["aud"] == "https://oauth2.googleapis.com/token"
    assert claims["exp"] == claims["iat"] + 3600
    # verify signature with the public key
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives import hashes
    pubkey.public_key().verify(
        _b64url_decode(sig_b64),
        f"{header_b64}.{claims_b64}".encode(),
        asym_padding.PKCS1v15(), hashes.SHA256())


def _b64url_decode(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_gsc_request_shape_with_fake_transport(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_JSON", json.dumps(_make_service_account()[0]))
    monkeypatch.setenv("GSC_SITE_URL", "sc-domain:aimbex.example")
    calls = []

    async def fake_transport(url, headers, body):
        calls.append((url, headers, body))
        if "oauth2.googleapis.com" in url:
            return {"access_token": "tok-123", "expires_in": 3600}
        return {"rows": [{"keys": ["water damage cost"], "clicks": 42, "impressions": 900,
                          "ctr": 0.0466, "position": 4.2}]}

    from app.integ.gsc import GSCAdapter
    from app.integ.google_oauth import GoogleOAuth
    adapter = GSCAdapter(oauth=GoogleOAuth(transport=fake_transport))
    res = asyncio.run(adapter.execute("demand.queries", {"start_date": "7daysAgo"}))
    assert res.ok and len(res.data["demand"]) == 1
    token_call, api_call = calls
    assert token_call[0] == "https://oauth2.googleapis.com/token"
    assert api_call[1]["Authorization"] == "Bearer tok-123"
    assert api_call[2]["dimensions"] == ["query"]
    assert res.data["demand"][0]["query"] == "water damage cost"
    assert res.data["demand"][0]["simulated"] is False


def test_gsc_fails_closed_without_creds(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_JSON", raising=False)
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    from app.integ.gsc import GSCAdapter
    adapter = GSCAdapter()
    assert asyncio.run(adapter.health_check()).ok is False
    res = asyncio.run(adapter.execute("demand.queries", {}))
    assert res.ok is False and any("not configured" in w for w in res.warnings)


def test_dev_demand_deterministic_and_honest():
    from app.integ.dev_demand import DevDemandAdapter
    adapter = DevDemandAdapter()
    a = asyncio.run(adapter.execute("demand.queries", {"locale": "CA-AB"})).data["demand"]
    b = asyncio.run(adapter.execute("demand.queries", {"locale": "CA-AB"})).data["demand"]
    assert len(a) == len(b) >= 25
    assert all(r["simulated"] is True for r in a)
    assert all("clicks" not in r and "impressions" not in r for r in a)  # no fabricated volumes
    keys_a = [r["evidence_key"] for r in a]
    keys_b = [r["evidence_key"] for r in b]
    assert keys_a != keys_b  # fresh evidence per run, deterministic shape
