"""Workflow A (blueprint §5): demand intelligence -> evidence store -> content briefs.

Cron-triggered weekly research: pull demand rows via the 'demand.pull' provider route
(dev_demand fallback -> real GSC), normalize + classify + evidence-store into
customer_questions (dedupe by normalized question), cluster by intent, then queue
content briefs for the top clusters. Everything evidence-backed; volumes are never
fabricated (simulated rows carry simulated=true).
"""
import datetime
import json
import uuid

from app.integ import router as provider_router
from app.schemas import AgentJobEnvelope
from app.services import jobs as jobs_service
from worker.handlers import classify_intent, cluster_by_intent, normalize_question


async def run_workflow_a(conn, tenant_id: uuid.UUID, locale: str = "CA-AB",
                         redis=None, limit_top: int = 3, run_key: str | None = None) -> dict:
    # 1) demand pull via data-driven route (dev_demand for keyless runs)
    adapter = await provider_router.route("demand.pull", conn=conn, tenant_id=tenant_id)
    res = await adapter.execute("demand.queries", {"locale": locale})
    if not res.ok:
        return {"status": "failed", "errors": res.warnings}
    rows = res.data["demand"]

    # 2) evidence store (idempotent by normalized question)
    inserted = 0
    for d in rows:
        q = normalize_question(d["query"])
        evidence = [{"source": d.get("source"), "simulated": d.get("simulated", False),
                     "fetched_at": d.get("fetched_at"), "evidence_key": d.get("evidence_key")}]
        if d.get("clicks") is not None:
            evidence[0]["clicks"] = d["clicks"]
        n = await conn.execute(
            "INSERT INTO customer_questions (tenant_id, normalized_question, intent, locale, evidence_json) "
            "VALUES ($1, $2, $3, $4, $5::jsonb) "
            "ON CONFLICT (tenant_id, normalized_question) DO NOTHING",
            tenant_id, q, classify_intent(q), locale, json.dumps(evidence))
        if n.startswith("INSERT"):
            inserted += int(n.split()[-1])

    # 3) cluster from the store, pick top intents
    store = await conn.fetch(
        "SELECT normalized_question, intent FROM customer_questions WHERE tenant_id = $1",
        tenant_id)
    clusters = cluster_by_intent([r["normalized_question"] for r in store])
    clusters.sort(key=lambda c: -c["count"])
    top = clusters[:limit_top]

    # 4) queue content briefs (idempotent per day+intent)
    queued = []
    for c in top:
        envelope = AgentJobEnvelope(
            agent="content_writer",
            goal=f"Article: {c['questions'][0]}",
            input_refs=["demand://", f"intent://{c['intent']}"],
            constraints={"max_cost_usd": 0.20, "region": locale},
            idempotency_key=f"draft:{run_key or datetime.date.today().isoformat()}:{c['intent']}")
        job = await jobs_service.create_job(conn, tenant_id, envelope, redis=redis)
        queued.append({"intent": c["intent"], "count": c["count"],
                       "job_id": job.get("id"), "duplicate": job.get("duplicate", False)})

    return {"status": "ok", "simulated": res.data.get("simulated", False),
            "pulled": len(rows), "inserted_new": inserted, "store_total": len(store),
            "top_clusters": top, "queued_briefs": queued, "warnings": res.warnings}
