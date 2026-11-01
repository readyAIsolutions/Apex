#!/usr/bin/env python3
"""APEX verification suite — proves the blueprint's acceptance criteria (§11) with
REAL execution against the REAL database. No mocks for the things that matter:
RLS denial, approval hash gate, webhook idempotency, consent opt-out, Odoo flow,
asset->approval->publish gate, and Workflow A demand->question->briefs.

Re-runnable: every mutating check uses a fresh key/row; tenant B is upserted.

Loop-safety: the app's connection pool is created ONLY inside the TestClient's own
event loop and closed there. All DB sections use dedicated apex_app connections
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

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

GOOD_HTML = ("<title>AI Receptionist for Restoration Firms in Edmonton</title>"
             "<meta name='description' content='AI receptionist and missed-call recovery for "
             "restoration and contracting firms in Edmonton and Lloydminster, Alberta.'>"
             "<meta name='viewport' content='width=device-width, initial-scale=1'>"
             "<link rel='canonical' href='https://aimbex.example/ai-receptionist'>"
             '<script type="application/ld+json">{"@type":"Service"}</script>'
             "<body><h1>Never Miss a Qualified Lead</h1>"
             + ("<p>word</p>" * 130) + "</body>")

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
    from app.services import leads as leads_svc
    from worker.handlers import cluster_by_intent

    migrator = await asyncpg.connect(host="127.0.0.1", user="apex_migrator",
                                     password="apex_migrator_dev_password", database="apex")

    print("== 0. bootstrap state ==")
    rows = await migrator.fetch("SELECT file FROM apex_migrations ORDER BY file")
    check("migrations applied (9)", len(rows) == 9, ", ".join(r["file"] for r in rows))
    rls_on = await migrator.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname='public' AND c.relrowsecurity AND c.relname NOT LIKE 'apex\\_%'")
    check("RLS enabled on tenant tables", rls_on is not None and rls_on >= 19, f"{rls_on} tables")
    keys = await migrator.fetch("SELECT name, roles FROM api_keys ORDER BY name")
    check("api keys seeded", len(keys) >= 1, f"{len(keys)} keys")

    # purge verify-generated contacts so repeated runs don't accumulate test data
    for t in (TENANT_AIMBEX, TENANT_B):
        await migrator.execute(
            "DELETE FROM appointments WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE full_name IN ('Secret B Contact','Opt Out User','Victor Restoration','Webhook Lead'))")
        await migrator.execute(
            "DELETE FROM opportunity_attribution WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE full_name IN ('Secret B Contact','Opt Out User','Victor Restoration','Webhook Lead'))")
        await migrator.execute(
            "DELETE FROM opportunities WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE full_name IN ('Secret B Contact','Opt Out User','Victor Restoration','Webhook Lead'))")
        await migrator.execute(
            "DELETE FROM consent_events WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE full_name IN ('Secret B Contact','Opt Out User','Victor Restoration','Webhook Lead'))")
        await migrator.execute(
            "DELETE FROM lead_events WHERE contact_id IN "
            "(SELECT id FROM contacts WHERE full_name IN ('Secret B Contact','Opt Out User','Victor Restoration','Webhook Lead'))")
        await migrator.execute(
            "DELETE FROM contacts WHERE full_name IN "
            "('Secret B Contact','Opt Out User','Victor Restoration','Webhook Lead') "
            "AND tenant_id = $1", t)

    await migrator.execute("INSERT INTO tenants (id, name) VALUES ($1,'Tenant B') "
                           "ON CONFLICT (id) DO NOTHING", TENANT_B)
    await migrator.execute(
        "INSERT INTO contacts (tenant_id, full_name, email_hash, consent_status) "
        "VALUES ($1,'Secret B Contact', encode(sha256('b-secret@example.com'::bytea),'hex'),'granted') "
        "ON CONFLICT DO NOTHING", TENANT_B)
    # second-tenant admin API key (P3 isolation proof)
    b_key = "b_tenant_admin_key"
    await migrator.execute(
        "INSERT INTO api_keys (tenant_id, name, key_hash, roles) VALUES "
        "($1, 'tenant_b_admin', $2, ARRAY['admin']) ON CONFLICT (key_hash) DO NOTHING",
        TENANT_B, hashlib.sha256(b_key.encode()).hexdigest())

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

        # Workflow B booking milestone: book a consultation slot (Odoo calendar mirror)
        opp_id = await conn.fetchval(
            "SELECT id FROM opportunities WHERE tenant_id = $1 AND contact_id = $2 "
            "ORDER BY created_at DESC LIMIT 1", TENANT_AIMBEX, UUID(outcome["contact_id"]))
        appt = await leads_svc.schedule_appointment(
            conn, TENANT_AIMBEX, UUID(outcome["contact_id"]), opp_id,
            datetime.now(timezone.utc) + timedelta(days=1), crm_adapter=odoo)
        check("appointment booked via Odoo calendar (mock)",
              bool(appt.get("calendar_external_id")), str(appt.get("calendar_external_id")))

    print("== 5. clustering (pure) ==")
    clusters = cluster_by_intent(["FLOOD damage emergency", "flood damage emergency", "cost of AI"])
    by_intent = {c["intent"]: c for c in clusters}
    check("intent clustering + dedupe",
          by_intent["emergency"]["count"] == 1 and by_intent["price"]["count"] == 1)

    print("== 6. webhook intake + API smoke + asset gate (HMAC, RBAC, dashboard, publish) ==")
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

        rseo_low = client.post("/v1/seo/audit", headers={"Authorization": "Bearer apex_dev_analyst_key"},
                               json={"html": GOOD_HTML})
        check("seo audit denied to analyst (editor+)", rseo_low.status_code == 403, str(rseo_low.status_code))
        rseo = client.post("/v1/seo/audit", headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                           json={"html": GOOD_HTML})
        check("seo audit endpoint returns checks",
              rseo.status_code == 200 and rseo.json().get("ok_all") is True, f"HTTP {rseo.status_code}")

        rads = client.get("/v1/ads/optimizer/recommend",
                          headers={"Authorization": "Bearer apex_dev_analyst_key"},
                          params={"campaign_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"})
        check("optimizer denied to analyst (marketer+)", rads.status_code == 403, str(rads.status_code))
        rads = client.get("/v1/ads/optimizer/recommend",
                          headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                          params={"campaign_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"})
        rads_json = rads.json() if rads.status_code == 200 else {}
        check("optimizer refuses tiny-sample scale",
              rads.status_code == 200 and rads_json.get("status") == "refuse_scale",
              f"status={rads_json.get('status')} reason={str(rads_json.get('reasons'))[:80]}")
        rqa = client.post("/v1/qa/content",
                          headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                          json={"text": "guaranteed results with a risk-free setup, 200% more calls"})
        check("qa flags banned ROI language",
              rqa.status_code == 200 and rqa.json().get("pass") is False, str(rqa.status_code))
        rqa2 = client.post("/v1/qa/content",
                           headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                           json={"text": "Answers missed calls and books consultations. Opt-out anytime."})
        check("qa passes clean copy", rqa2.status_code == 200 and rqa2.json().get("pass") is True,
              str(rqa2.status_code))

        rjob = client.post("/v1/agents/jobs",
                           headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                           json={"agent": "crm_sync", "goal": "sync contacts",
                                 "idempotency_key": f"verify:job:{uuid.uuid4().hex[:8]}"})
        check("agent job enqueued", rjob.status_code == 202, str(rjob.status_code))
        job_id = rjob.json()["id"]

        ar = client.post("/v1/actions/00000000-0000-4000-8000-000000000000/execute",
                         headers={"Authorization": "Bearer apex_dev_executor_key"})
        check("action executor route reachable (404 on unknown)", ar.status_code == 404, str(ar.status_code))

        # ---- E2E human gate over HTTP: create draft -> submit -> approve -> execute -> receipt ----
        cre_r = client.post("/v1/assets", headers={"Authorization": "Bearer apex_dev_analyst_key"},
                            json={"kind": "article"})
        check("analyst cannot create asset (editor+)", cre_r.status_code == 403, str(cre_r.status_code))
        cre_r = client.post("/v1/assets", headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                            json={"kind": "article"})
        gate_asset_id = cre_r.json()["asset_id"]
        sub_r = client.post(f"/v1/assets/{gate_asset_id}/submit",
                            headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                            json={"platform": "site"})
        gate_approval_id = sub_r.json()["approval_id"]
        plist = client.get("/v1/approvals?state=pending",
                           headers={"Authorization": "Bearer apex_dev_approver_key"})
        check("approval queue lists pending gate",
              plist.status_code == 200 and any(a["action_type"].startswith("publish:") for a in plist.json()),
              str(plist.status_code))
        apr_r = client.post(f"/v1/approvals/{gate_approval_id}/decision",
                            headers={"Authorization": "Bearer apex_dev_approver_key"},
                            json={"decision": "approve"})
        check("approver approves asset",
              apr_r.status_code == 200 and apr_r.json()["decision"] == "approved", str(apr_r.status_code))
        exec_r = client.post(f"/v1/actions/{gate_approval_id}/execute",
                             headers={"Authorization": "Bearer apex_dev_executor_key"})
        if exec_r.status_code == 200:
            exec_ok = exec_r.json().get("decision") == "executed" and "receipt" in exec_r.json()
        else:
            exec_ok = False
        check("executor publishes with receipt", exec_ok,
              f"HTTP {exec_r.status_code}: {exec_r.text[:160]}")

        # Compliance/QA as a precondition on the publish gate
        bad_asset = client.post("/v1/assets", headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                                json={"kind": "article"}).json()["asset_id"]
        q_reject = client.post(f"/v1/assets/{bad_asset}/submit",
                               headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                               json={"platform": "site", "text": "guaranteed results with a risk-free setup"})
        check("submit rejected when QA fails",
              q_reject.status_code == 409
              and q_reject.json().get("detail", {}).get("error") == "qa_failed",
              f"HTTP {q_reject.status_code} body={q_reject.text[:80]}")
        q_force = client.post(f"/v1/assets/{bad_asset}/submit",
                              headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                              json={"platform": "site", "text": "guaranteed results with a risk-free setup",
                                    "force": True})
        check("force override submits despite QA fail", q_force.status_code == 201,
              f"HTTP {q_force.status_code}")
        cle_ass = client.post("/v1/assets", headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                              json={"kind": "article"}).json()["asset_id"]
        q_clean = client.post(f"/v1/assets/{cle_ass}/submit",
                              headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                              json={"platform": "site", "text": "We answer missed calls and book consultations. Opt-out anytime."})
        check("clean copy submits (QA pass)", q_clean.status_code == 201 and q_clean.json()["qa"]["pass"] is True,
              f"HTTP {q_clean.status_code}")

        # Optimizer-guarded budget change (§10) + second-tenant isolation (P3)
        bud_up = client.post("/v1/campaigns/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1/budget",
                             headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                             json={"budget_cap_minor": 300000})
        check("budget increase blocked by optimizer",
              bud_up.status_code == 409 and bud_up.json().get("detail", {}).get("error") == "budget_increase_blocked",
              f"HTTP {bud_up.status_code}")
        bud_dn = client.post("/v1/campaigns/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1/budget",
                             headers={"Authorization": "Bearer apex_dev_admin_key_change_me"},
                             json={"budget_cap_minor": 100000})
        check("budget decrease allowed",
              bud_dn.status_code == 200 and bud_dn.json().get("increased") is False,
              f"HTTP {bud_dn.status_code}")
        bdash = client.get("/v1/dashboard/overview", headers={"Authorization": "Bearer b_tenant_admin_key"})
        bjs = bdash.json() if bdash.status_code == 200 else {}
        check("second tenant sees only its own (empty) data",
              bdash.status_code == 200 and bjs.get("spend_usd") == 0 and bjs.get("wins") == 0,
              f"HTTP {bdash.status_code} spend={bjs.get('spend_usd')}")
        bsub = client.post(f"/v1/assets/{gate_asset_id}/submit",
                           headers={"Authorization": "Bearer b_tenant_admin_key"},
                           json={"platform": "site"})
        check("second tenant cannot touch tenant A's asset", bsub.status_code == 404,
              f"HTTP {bsub.status_code}")

    # ---- worker runs AFTER TestClient exits (fresh pool in main loop, no cross-loop) ----
    from worker.run import run_once
    n = await run_once()
    check("worker processed job", n >= 1, f"{n} job(s)")
    async with app_scope(TENANT_AIMBEX) as conn:
        jr = await conn.fetchrow("SELECT state, output_json FROM agent_jobs WHERE id = $1",
                                 UUID(job_id))
    check("job completed", jr is not None and jr["state"] == "succeeded", jr["state"] if jr else "missing")

    # E2E gate side-effects (publication + asset status)
    async with app_scope(TENANT_AIMBEX) as conn:
        pub = await conn.fetchval("SELECT count(*) FROM publications WHERE asset_id = $1",
                                  UUID(gate_asset_id))
        ast = await conn.fetchval("SELECT status FROM content_assets WHERE id = $1",
                                  UUID(gate_asset_id))
    check("publication receipt recorded", pub == 1, f"publications={pub}")
    check("asset flipped to published", ast == "published", str(ast))

    print("== 7. Workflow A end-to-end (demand -> question store -> briefs) ==")
    import redis.asyncio as aioredis
    from app.services.workflow_a import run_workflow_a
    r = aioredis.from_url(os.environ["REDIS_URL"])
    try:
        async with app_scope(TENANT_AIMBEX) as conn:
            wfa = await run_workflow_a(conn, TENANT_AIMBEX, "CA-AB", redis=r,
                                       run_key=f"wf-{uuid.uuid4().hex[:8]}")
        n2 = await run_once()
        async with app_scope(TENANT_AIMBEX) as conn:
            q_total = await conn.fetchval(
                "SELECT count(*) FROM customer_questions WHERE tenant_id = $1", TENANT_AIMBEX)
            article_count = await conn.fetchval(
                "SELECT count(*) FROM content_assets WHERE tenant_id = $1 AND kind = 'article'",
                TENANT_AIMBEX)
            drafts = await conn.fetch(
                "SELECT id, state FROM agent_jobs WHERE tenant_id = $1 AND agent = 'content_writer' LIMIT 10",
                TENANT_AIMBEX)
    finally:
        await r.aclose()
    check("workflow A ran ok", wfa["status"] == "ok" and wfa["pulled"] >= 25, f"pulled={wfa.get('pulled')}")
    check("question store populated", q_total >= 25, f"store={q_total}")
    check("top clusters produced", len(wfa["top_clusters"]) >= 1, f"clusters={len(wfa['top_clusters'])}")
    check("worker drained workflow jobs", n2 >= 1, f"drained={n2}")
    check("brief jobs completed (keyless = template fallback)",
          bool(drafts) and all(d["state"] == "succeeded" for d in drafts), f"{len(drafts)} drafts ok")
    check("briefs produced draft content assets", article_count >= 1, f"articles={article_count}")
    for c in wfa["top_clusters"]:
        print(f"      - {c['intent']:13s} n={c['count']:3d}  e.g. \"{c['questions'][0]}\"")
    for w in wfa["warnings"]:
        print(f"      warning: {w}")

    print("== 8. SEO auditor + attribution reconcile (§4/§10) ==")
    import redis.asyncio as aioredis
    from app.schemas import AgentJobEnvelope
    from app.services import jobs as jobs_service
    r = aioredis.from_url(os.environ["REDIS_URL"])
    try:
        async with app_scope(TENANT_AIMBEX) as conn:
            seo_job = await jobs_service.create_job(
                conn, TENANT_AIMBEX,
                AgentJobEnvelope(agent="seo_auditor", goal="Audit the AI receptionist landing page",
                                 input={"html": GOOD_HTML},
                                 idempotency_key=f"verify:seo:{uuid.uuid4().hex[:8]}"), redis=r)
            att_job = await jobs_service.create_job(
                conn, TENANT_AIMBEX,
                AgentJobEnvelope(agent="attribution", goal="Reconcile closed-won to original source",
                                 idempotency_key=f"verify:attr:{uuid.uuid4().hex[:8]}"), redis=r)
            seo_id, att_id = seo_job["id"], att_job["id"]
        n3 = await run_once()
        async with app_scope(TENANT_AIMBEX) as conn:
            seo_row = await conn.fetchrow(
                "SELECT state, output_json FROM agent_jobs WHERE id = $1", UUID(seo_id))
            seo_res = json.loads(seo_row["output_json"]) if seo_row and seo_row["output_json"] else {}
            att_rows = await conn.fetch(
                "SELECT source, campaign_id FROM opportunity_attribution WHERE tenant_id = $1",
                TENANT_AIMBEX)
    finally:
        await r.aclose()
    check("seo audit job completed", seo_row is not None and seo_row["state"] == "succeeded",
          seo_row["state"] if seo_row else "missing")
    check("seo job produced checks", bool(seo_res.get("structured_output", {}).get("checks")),
          f"ok_all={seo_res.get('structured_output', {}).get('ok_all')}")
    check("attribution job ran", n3 >= 1, f"jobs drained={n3}")
    check("attribution joined won deal to source",
          bool(att_rows) and any(r["source"] == "ad" for r in att_rows),
          f"{len(att_rows)} attributed: {[r['source'] for r in att_rows]}")

    print("== 9. Paid media optimizer + compliance QA job E2E (§11 P1) ==")
    r = aioredis.from_url(os.environ["REDIS_URL"])
    try:
        async with app_scope(TENANT_AIMBEX) as conn:
            pm_job = await jobs_service.create_job(
                conn, TENANT_AIMBEX,
                AgentJobEnvelope(agent="paid_media", goal="Recommend-only spend review",
                                 idempotency_key=f"verify:pm:{uuid.uuid4().hex[:8]}"), redis=r)
            qa_job = await jobs_service.create_job(
                conn, TENANT_AIMBEX,
                AgentJobEnvelope(agent="content_qa", goal="QA the draft",
                                 input={"text": "guaranteed results with a risk-free setup"},
                                 idempotency_key=f"verify:qa:{uuid.uuid4().hex[:8]}"), redis=r)
            pm_id, qa_id = pm_job["id"], qa_job["id"]
        n4 = await run_once()
        async with app_scope(TENANT_AIMBEX) as conn:
            pm_row = await conn.fetchrow("SELECT output_json FROM agent_jobs WHERE id = $1",
                                         UUID(pm_id))
            qa_row = await conn.fetchrow("SELECT output_json FROM agent_jobs WHERE id = $1",
                                         UUID(qa_id))
    finally:
        await r.aclose()
    pm_out = json.loads(pm_row["output_json"])["structured_output"] if pm_row and pm_row["output_json"] else {}
    qa_out = json.loads(qa_row["output_json"])["structured_output"] if qa_row and qa_row["output_json"] else {}
    pm_recs = pm_out.get("recommendations", [])
    check("optimizer job refuses unsafe scale",
          pm_out.get("any_refused_scale") is True and any(
              rec["status"] == "refuse_scale" for rec in pm_recs), f"{len(pm_recs)} campaigns")
    check("optimizer reason is honest (sample)", bool(pm_recs) and any(
        "sample too small" in r_ for rec in pm_recs for r_ in rec["reasons"]))
    check("qa job flags banned language", qa_out.get("pass") is False, str(qa_out.get("findings"))[:80])

    print("== 10. Backup/restore round-trip + ops check (§11 P0 / §8) ==")
    from app.services import backup as backup_svc
    snap = await backup_svc.create_snapshot()          # logical snapshot (asyncpg-only)
    snap_tenants = snap["manifest"]["tables"]["tenants"]["rows"]
    check("snapshot created", snap_tenants >= 2, f"tenants={snap_tenants} path={snap['path']}")
    restore = await backup_svc.restore_snapshot(snap["path"])
    check("restore round-trip",
          restore["restored"].get("tenants", 0) >= 2 and restore["restored"].get("contacts", 0) >= 1,
          f"tenants={restore['restored'].get('tenants')} contacts={restore['restored'].get('contacts')}")
    mig2 = await asyncpg.connect(host="127.0.0.1", user="apex_migrator",
                                 password="apex_migrator_dev_password",
                                 database="apex_restore_test")
    b_rows = await mig2.fetchval("SELECT count(*) FROM contacts WHERE tenant_id = $1", TENANT_B)
    b_marker = await mig2.fetchval(
        "SELECT count(*) FROM contacts WHERE tenant_id = $1 AND full_name = 'Secret B Contact'",
        TENANT_B)
    rls2 = await mig2.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relrowsecurity")
    await mig2.close()
    check("restored snapshot keeps cross-tenant rows", b_rows >= 1 and b_marker == 1,
          f"B contacts={b_rows} marker={b_marker}")
    check("restored schema has RLS policies", rls2 >= 19, f"rls_tables={rls2}")
    await backup_svc.drop_scratch()

    sys.path.insert(0, str(ROOT / "scripts"))
    import ops_check
    oc = await ops_check.collect()
    disk_low_detected = any("disk low" in a for a in oc.get("alerts", []))
    check("ops check detects disk saturation correctly",
          disk_low_detected == (oc["disk_free_pct"] < 10),
          f"free={oc.get('disk_free_pct')}% alerts={oc.get('alerts')}")
    check("ops metrics present",
          "queue_depth" in oc and "disk_free_gb" in oc and "backup_age_h" in oc,
          f"backup_age_h={oc.get('backup_age_h')} queue={oc.get('queue_depth')}")

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