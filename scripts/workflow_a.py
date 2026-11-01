#!/usr/bin/env python3
"""Workflow A CLI (§5): weekly demand research -> question store -> content briefs -> worker.

Usage:
  .venv/bin/python scripts/workflow_a.py [--locale CA-AB] [--no-worker]

Runs the crawl-route 'demand.pull' (dev_demand simulated by default; point
provider_routes.demand.pull at gsc when credentials exist), stores evidence,
queues briefs, then drains the queue once with the worker. Prints a report.
"""
import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql://apex_app:apex_app_dev_password@127.0.0.1:5432/apex")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/4")
os.environ.setdefault("ODOO_MOCK", "1")

TENANT_AIMBEX = uuid.UUID("11111111-1111-4111-8111-111111111111")


async def main() -> int:
    args = argparse.ArgumentParser(description="APEX Workflow A")
    args.add_argument("--locale", default=os.environ.get("WF_LOCALE", "CA-AB"))
    args.add_argument("--no-worker", action="store_true", help="queue briefs but don't drain")
    opts = args.parse_args()

    import redis.asyncio as aioredis
    from app.config import settings
    from app.db import TenantScope
    from app.services.workflow_a import run_workflow_a

    r = aioredis.from_url(settings.redis_url)
    try:
        async with TenantScope(TENANT_AIMBEX) as conn:
            report = await run_workflow_a(conn, TENANT_AIMBEX, opts.locale, redis=r)
        if opts.no_worker:
            n = 0
        else:
            from worker.run import run_once
            n = await run_once()
    finally:
        await r.aclose()

    print("== Workflow A report ==")
    if report["status"] != "ok":
        print(f"FAILED: {report['errors']}")
        return 1
    print(f"  demand rows pulled : {report['pulled']} (simulated={report['simulated']})")
    print(f"  new evidence rows  : {report['inserted_new']}")
    print(f"  question store     : {report['store_total']}")
    print(f"  briefs queued      : {len(report['queued_briefs'])} (worker drained {n})")
    for c in report["top_clusters"]:
        print(f"    - {c['intent']:13s} n={c['count']:3d}  e.g. \"{c['questions'][0]}\"")
    for w in report["warnings"]:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
