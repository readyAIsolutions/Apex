"""Unit tests (fast, no DB): hashes, clustering, mock Odoo adapter, router routing."""
import asyncio

import pytest

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
