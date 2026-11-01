#!/usr/bin/env python3
"""Workflow Conversion-Intel CLI (§4/§14): filter bad funnels/copy -> grade evidence A-D
-> evolve ORIGINAL client variants -> record reusable playbooks. Runs keyless on the
deterministic campaign_corpus (swap 'research.pull' route to a real ads-library when
credentialed). Each step writes tenant-scoped rows; nothing auto-publishes.

Usage:
  .venv/bin/python scripts/workflow_conv.py [--region CA-AB] [--brand '{"industry": "..."}']
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# NOTE: do NOT set DATABASE_URL to a placeholder here — it would override .env's real
# password. settings reads .env; only ODOO_MOCK default is safe to force.
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/4")
os.environ.setdefault("ODOO_MOCK", "1")

TENANT_AIMBEX = uuid.UUID("11111111-1111-4111-8111-111111111111")


async def _run_agent(conn, tenant, agent: str, input_: dict, r) -> str:
    from app.schemas import AgentJobEnvelope
    from app.services import jobs as jobs_service
    env = AgentJobEnvelope(agent=agent, goal=f"Conversion intel ({agent})",
                           input=input_, idempotency_key=f"wfconv:{agent}:{uuid.uuid4().hex[:6]}")
    job = await jobs_service.create_job(conn, tenant, env, redis=r)
    return job["id"]


async def main() -> int:
    ap = argparse.ArgumentParser(description="APEX Conversion Intelligence workflow")
    ap.add_argument("--region", default="CA-AB")
    ap.add_argument("--brand", default=json.dumps({
        "industry": "B2B AI automation for contractors",
        "service": "AI receptionist / missed-call recovery",
        "area": "Lloydminster & Edmonton"}))
    ap.add_argument("--no-worker", action="store_true")
    opts = ap.parse_args()

    import redis.asyncio as aioredis
    from app.config import settings
    from app.db import TenantScope
    from app.integ import router as provider_router

    r = aioredis.from_url(settings.redis_url)
    try:
        async with TenantScope(TENANT_AIMBEX) as conn:
            # 0) sanity: confirm a research source is routed (corpus -> real lib when live)
            adapter = await provider_router.route("research.pull", conn=conn, tenant_id=TENANT_AIMBEX)
            src_name = adapter.name
            # 1) queue the engine agents
            ids = {}
            for agent, inp in [
                ("campaign_hunter", {"region": opts.region}),
                ("evidence_auditor", {}),
                ("creative_evolution", {"brand": json.loads(opts.brand)}),
                ("ab_test_designer", {"variants": ["v-a", "v-b"]}),
                ("winning_strategy_memory", {}),
            ]:
                ids[agent] = await _run_agent(conn, TENANT_AIMBEX, agent, inp, r)
        n = 0 if opts.no_worker else None
        if not opts.no_worker:
            from worker.run import run_once
            n = await run_once()
        async with TenantScope(TENANT_AIMBEX) as conn:
            gc = {row["grade"]: row["n"] for row in await conn.fetch(
                "SELECT grade, count(*)::int AS n FROM conversion_evidence "
                "WHERE tenant_id = $1 GROUP BY grade", TENANT_AIMBEX)}
            research = await conn.fetchval("SELECT count(*) FROM campaign_research WHERE tenant_id = $1",
                                           TENANT_AIMBEX)
            variants = await conn.fetchval("SELECT count(*) FROM creative_experiments WHERE tenant_id = $1",
                                           TENANT_AIMBEX)
            playbooks = await conn.fetchval("SELECT count(*) FROM conversion_playbooks WHERE tenant_id = $1",
                                            TENANT_AIMBEX)
    finally:
        await r.aclose()

    print("== Conversion Intelligence workflow report ==")
    is_sim = src_name == 'campaign_corpus'
    print(f"  research source    : {src_name} ({'SIMULATED - swap route to a real ads-lib for live' if is_sim else 'live'})")
    print(f"  campaigns stored   : {research}   evidence by grade: {gc or '{}'}")
    print(f"  hype demoted (D)   : {gc.get('D', 0)}  (bad funnels/copy filtered out)")
    keepers = gc.get('A', 0) + gc.get('B', 0)
    print(f"  provable keepers   : {keepers} (A/B — used as creative principles)")
    print(f"  original variants  : {variants}   playbooks: {playbooks}")
    print(f"  worker drained     : {n} job(s)")
    print("  NOTE: every variant is a hypothesis to test — never auto-published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))