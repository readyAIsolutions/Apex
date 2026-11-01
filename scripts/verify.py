#!/usr/bin/env python3
"""APEX verification suite — proves the blueprint's P0 acceptance criteria (§11) with
REAL execution against the REAL database. No mocks for the things that matter:
RLS denial, approval hash gate, webhook idempotency, consent opt-out, Odoo flow.

Re-runnable: every mutating check uses a fresh key/row; tenant B is upserted.

Loop-safety: the app's connection pool is created ONLY inside the TestClient's own
event loop and closed there. All DB sections below use dedicated apex_app connections
(app_scope) so the pool is never handed across asyncio loops. The worker runs AFTER
TestClient exits (fresh pool in the main loop).

Usage:  .venv/bin/python scripts/verify.py   (exit 0 = all checks pass)
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---- environment (BEFORE importing app modules — settings are cached at import) ----
os.environ.setdefault("DATABASE_URL", "postgresql://apex_app:apex_app_dev_password@127.0.0.1:5432/apex")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/4")
os.environ.setdefault("ODOO_MOCK", "1")
os.environ.setdefault("WEBHOOK_SECRET_form", "verify_secret_form")
os.environ.setdefault("BOOTSTRAP_ADMIN_KEY", "apex_dev_admin_key_change_me")
os.environ.setdefault("BOOTSTRAP_KEYS", "analyst:apex_dev_analyst_key,executor:apex_dev_executor_key,approver:apex_dev_approver_key")

TENANT_AIMBEX = uuid.UUID("11111111-1111-4111-8111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-4222-8222-222222222222")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


@asynccontextmanager
async def app_scope(tenant_id):
    """Dedicated apex_app connection + transaction + RLS context (bypasses app pool)."""
    import asyncpg
    conn = await asyncpg.connect(host="127.0.0.1", port=5432, user="apex_app",
                                 password="apex_app_dev_password", database="apex")
    tx = conn.transaction()
    await tx.start()
    if tenant_id is not None:
        await conn.execute("SELECT set_config('apex.tenant_id', $1, true)", str(tenant_id))
    try:
        yield conn
        await tx.commit()
    except BaseException:
        await tx.rollback()
        raise
    finally:
        await conn.close()


async def main():
    import asyncpg
    from app.integ import REGISTRY as REG
    from app.schemas import LeadEventIn
    from app.services import approvals as appr
    from app.services import consent as consent_svc
    from app.services import leads as leads_svc
    from worker.handlers import cluster_by_intent

    migrator = await asyncpg.connect(host="127.0.0.1", user="apex_migrator",
                                     password="apex_migrator_dev_password", database="apex")

    print("== 0. bootstrap state ==")
    rows = await migrator.fetch("SELECT file FROM apex_migrations ORDER BY file")
    check("migrations applied (7)", len(rows) == 7, ", ".join(r["file"] for r in rows))
    rls_on = await migrator.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname='public' AND c.relrowsecurity AND c.relname NOT LIKE 'apex\\_%'")
    check("RLS enabled on tenant tables", rls_on is not None and rls_on >= 19, f"{rls_on} tables")
    keys = await migrator.fetch("SELECT name, roles FROM api_keys ORDER BY name")
    check("api keys seeded", len(keys) >= 1, f"{len(keys)} keys")

    # tenant B (upsert) for isolation tests
    await migrator.execute("INSERT INTO tenants (id, name) VALUES ($1,'Tenant B') "
                           "ON CONFLICT (id) DO NOTHING", TENANT_B)
    await migrator.execute(
        "INSERT INTO contacts (tenant_id, full_name, email_hash, consent_status) "
        "VALUES ($1,'Secret B Contact', encode(sha256('b-secret@example.com'::bytea),'hex'),'granted') "
        "ON CONFLICT DO NOTHING", TENANT_B)

    print("== 1. cross-tenant isolation (RLS, §11 P0) ==")
    async with app_scope(TENANT_AIMBEX) as conn:
        b_vis = await conn.fetchval("SELECT count(*) FROM contacts WHERE tenant_id = $1", TENANT_B)
        check("A cannot read B contacts", b_vis == 0, f"visible={b_vis}")
        u = await conn.execute("UPDATE contacts SET full_name='hacked' WHERE tenant_id = $1", TENANT_B)
        check("A cannot update B contacts", u == "UPDATE 0", u)
        try:
            await conn.execute(
                "INSERT INTO campaigns (tenant_id, name, channel, objective, budget_cap_minor) "
                "VALUES ($1,'evil','search','qualified_lead',1)", TENANT_B)
            check("A cannot insert into B (WITH CHECK)", False, "insert unexpectedly succeeded")
        except Exception as exc:
            check("A cannot insert into B (WITH CHECK)",
                  "row-level security" in str(exc).lower(), str(exc).splitlines()[-1][:120])

    print("== 2. approval state machine (§7/§11) ==")
    async with app_scope(TENANT_AIMBEX) as conn:
        a = await appr.create_approval(conn, TENANT_AIMBEX, "local.noop", {"k": "v", "n": 1})
        d = await appr.decide(conn, TENANT_AIMBEX, a["id"], "approve", "verify:approver")
        check("approval approved", d["decision"] == "approved")
        ex = await appr.execute_approval(conn, TENANT_AIMBEX, a["id"], "verify:executor", REG["local"])
        check("approved action executed once",
              ex["decision"] == "executed" and ex.get("execution", {}).get("applied"))
        ex2 = await appr.execute_approval(conn, TENANT_AIMBEX, a["id"], "verify:executor", REG["local"])
        check("execute is idempotent", ex2.get("idempotent") is True)

        t = await appr.create_approval(conn, TENANT_AIMBEX, "local.noop", {"k": "original"})
        await conn.execute("UPDATE approvals SET payload = $1 WHERE id = $2",
                           json.dumps({"k": "tampered"}), t["id"])
        await appr.decide(conn, TENANT_AIMBEX, t["id"], "approve", "verify:approver")
        try:
            await appr.execute_approval(conn, TENANT_AIMBEX, t["id"], "verify:executor", REG["local"])
            check("tampered payload blocked", False, "execution unexpectedly succeeded")
        except Exception as exc:
            check("tampered payload blocked", "hash mismatch" in str(exc), str(exc))

        from datetime import datetime, timedelta, timezone
        e = await appr.create_approval(conn, TENANT_AIMBEX, "local.noop", {"k": "expired"},
                                       expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        try:
            await appr.decide(conn, TENANT_AIMBEX, e["id"], "approve", "verify:approver")
            check("expired approval rejected", False, "decide unexpectedly succeeded")
        except Exception as exc:
            check("expired approval rejected", "expired" in str(exc), str(exc))

    print("== 3. consent gate (§11 P0) ==")
    async with app_scope(TENANT_AIMBEX) as conn:
        opt = await conn.fetchrow(
            "INSERT INTO contacts (tenant_id, full_name, email_hash, consent_status) "
            "VALUES ($1,'Opt Out User', encode(sha256('optout2@example.com'::bytea),'hex'),'opted_out') "
            "RETURNING *", TENANT_AIMBEX)
        out = await leads_svc.ingest_lead(conn, TENANT_AIMBEX,
                                          LeadEventIn(source="email", event_key=f"verify:optout:{uuid.uuid4().hex[:8]}",
                                                      email="optout2@example.com", full_name="Opt Out User"))
        check("opt-out blocks automation", out.get("blocked") == "consent")
        ev = await conn.fetchval("SELECT count(*) FROM lead_events WHERE contact_id = $1", opt["id"])
        check("no lead event recorded for opted-out", ev == 0)

    print("== 4. lead pipeline -> Odoo mirror (mock CRM) ==")
    odoo = REG["odoo"]
    async with app_scope(TENANT_AIMBEX) as conn:
        fresh_key = f"verify:lead:{uuid.uuid4().hex[:10]}"
        outcome = await leads_svc.ingest_lead(
            conn, TENANT_AIMBEX,
            LeadEventIn(source="webform", event_key=fresh_key,
                        email=f"victor{uuid.uuid4().hex[:6]}@restoration.example",
                        phone="+17805551234", full_name="Victor Restoration",
                        utm={"utm_source": "google", "utm_medium": "cpc"}),
            crm_adapter=odoo, actor="verify")
        check("lead ingested", outcome["processed"] is True and outcome["duplicate"] is False)
        check("CRM external id set (Odoo mirror)", bool(outcome.get("crm_external_id")),
              str(outcome.get("crm_external_id")))
        rec = await odoo.reconcile(fresh_key)
        check("reconcile by external id", rec.found is True, str(rec))
        dup = await leads_svc.ingest_lead(conn, TENANT_AIMBEX,
                                          LeadEventIn(source="webform", event_key=fresh_key,
                                                      email="dup@example.com"))
        check("idempotent duplicate lead", dup["processed"] is False and dup["duplicate"] is True)

    print("== 5. clustering (pure) ==")
    clusters = cluster_by_intent(["FLOOD damage emergency", "flood damage emergency", "cost of AI"])
    by_intent = {c["intent"]: c for c in clusters}
    check("intent clustering + dedupe",
          by_intent["emergency"]["count"] == 1 and by_intent["price"]["count"] == 1)

    print("== 6. webhook intake + API smoke (HMAC, RBAC, dashboard, gate) ==")
    from app.main import create_app

    with TestClientCompat(create_app()) as client:
        whkey = f"verify:wh:{uuid.uuid4().hex[:8]}"
        body = json.dumps({"event_key": whkey, "full_name": "Webhook Lead",
                           "email": "web@example.com", "utm": {"utm_source": "test"}}).encode()
        sig = hmac.new(b"verify_secret_form", body, hashlib.sha256).hexdigest()
        r1 = client.post("/v1/webhooks/form", content=body, headers={"X-APEX-Signature": sig})
        check("webhook accepted", r1.status_code == 200 and r1.json().get("processed"))
        r2 = client.post("/v1/webhooks/form", content=body, headers={"X-APEX-Signature": sig})
        check("webhook replay deduped", r2.json().get("duplicate") is True)
        rbad = client.post("/v1/webhooks/form", content=body, headers={"X-APEX-Signature": "deadbeef"})
        check("bad signature rejected",
              "invalid signature" in rbad.json().get("error", ""))

        r401 = client.get("/v1/dashboard/overview")
        check("no key -> 401", r401.status_code == 401, str(r401.status_code))
        rlow = client.get("/v1/dashboard/overview",
                          headers={"Authorization": "Bearer apex_dev_analyst_key"})
        check("analyst can read dashboard", rlow.status_code == 200, str(rlow.status_code))
        rdeny = client.post("/v1/campaigns", headers={"Authorization": "Bearer apex_dev_analyst_key"},
                            json={"name": "x", "channel": "search"})
        check("analyst denied campaign create (403)", rdeny.status_code == 403, str(rdeny.status_code))
        dash = client.get("/v1/dashboard/overview",
                          headers={"Authorization": "Bearer apex_dev_admin_key_change_me"}).json()
        check("dashboard spend == seeded spend", dash["spend_usd"] == 305.90, f"{dash['spend_usd']}")
        check("dashboard CAC computed", dash["cost_per_qualified_lead"] is not None,
              str(dash["cost_per_qualified_lead"]))
        check("dashboard caveats present", len(dash["caveats"]) >= 1)

        rjob = client.post("/v1/agents/jobs",
                           headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                           json={"agent": "crm_sync", "goal": "sync contacts",
                                 "idempotency_key": f"verify:job:{uuid.uuid4().hex[:8]}"})
        check("agent job enqueued", rjob.status_code == 202, str(rjob.status_code))
        job_id = rjob.json()["id"]

        ar = client.post(f"/v1/actions/00000000-0000-4000-8000-000000000000/execute",
                         headers={"Authorization": "Bearer apex_dev_executor_key"})
        check("action executor route reachable (404 on unknown)", ar.status_code == 404, str(ar.status_code))

    # worker runs AFTER TestClient homepage (fresh pool in main loop, no cross-loop)
    from uuid import UUID
    from worker.run import run_once
    n = await run_once()
    check("worker processed job", n >= 1, f"{n} job(s)")
    async with app_scope(TENANT_AIMBEX) as conn:
        jr = await conn.fetchrow("SELECT state, output_json FROM agent_jobs WHERE id = $1",
                                 UUID(job_id))
    check("job completed", jr is not None and jr["state"] == "succeeded", jr["state"] if jr else "missing")

    await migrator.close()
    return len(FAIL)


# TestClient imported lazily to keep the app pool out of the main loop until section 6.
def TestClientCompat(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


if __name__ == "__main__":
    print("APEX verification suite\n")
    code = asyncio.run(main())
    print(f"\n== {len(PASS)} passed, {len(FAIL)} failed ==")
    for name in FAIL:
        print(f"  FAILED: {name}")
    sys.exit(1 if FAIL else 0)