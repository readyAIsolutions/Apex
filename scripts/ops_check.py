#!/usr/bin/env python3
"""APEX ops health check (§8 operations): queue backlog, failed/unprocessed webhooks,
stalled jobs, DB/disk saturation, backup freshness. Prints a report; exit 1 on alerts.
Usage: .venv/bin/python scripts/ops_check.py   (or as a cron; quiet when all green)
"""
import asyncio
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql://apex_app:apex_app_dev_password@127.0.0.1:5432/apex")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/4")

MIGRATOR = {"host": "127.0.0.1", "port": 5432, "user": "apex_migrator",
            "password": "apex_migrator_dev_password"}
BACKUP_DIR = Path(os.environ.get("APEX_BACKUP_DIR", "/home/nova/apex/backups"))


async def collect() -> dict:
    import asyncpg
    import redis.asyncio as aioredis
    from app.config import settings

    checks = {}
    conn = await asyncpg.connect(database="apex", **MIGRATOR)
    try:
        checks["webhooks_unprocessed_1h"] = await conn.fetchval(
            "SELECT count(*) FROM webhook_events WHERE processed_at IS NULL "
            "AND received_at < now() - interval '1 hour'")
        checks["jobs_stalled_2h"] = await conn.fetchval(
            "SELECT count(*) FROM agent_jobs WHERE state IN ('queued','running') "
            "AND created_at < now() - interval '2 hours'")
        checks["db_size_mb"] = round((await conn.fetchval(
            "SELECT pg_database_size('apex')")) / 1048576, 1)
    finally:
        await conn.close()

    r = aioredis.from_url(settings.redis_url)
    try:
        checks["queue_depth"] = await r.llen(settings.job_queue)
    finally:
        await r.aclose()

    disk = shutil.disk_usage("/")
    checks["disk_free_gb"] = round(disk.free / 1073741824, 1)
    checks["disk_free_pct"] = round(disk.free / disk.total * 100, 1)

    snaps = sorted(BACKUP_DIR.glob("apex_snapshot_*.jsonl.gz"), reverse=True)
    if snaps:
        age_h = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.datetime.fromtimestamp(snaps[0].stat().st_mtime, datetime.timezone.utc))
        checks["backup_age_h"] = round(age_h.total_seconds() / 3600, 1)
        checks["backup_count"] = len(snaps)
    else:
        checks["backup_age_h"] = None
        checks["backup_count"] = 0

    alerts = []
    if checks["queue_depth"] and checks["queue_depth"] > 50:
        alerts.append(f"queue backlog: {checks['queue_depth']} jobs")
    if checks["jobs_stalled_2h"]:
        alerts.append(f"{checks['jobs_stalled_2h']} jobs stalled 2h+")
    if checks["webhooks_unprocessed_1h"]:
        alerts.append(f"{checks['webhooks_unprocessed_1h']} webhooks unprocessed 1h+")
    if checks["disk_free_pct"] < 10:
        alerts.append(f"disk low: {checks['disk_free_pct']}% free")
    if checks["backup_age_h"] is None or checks["backup_age_h"] > 24 * 7:
        alerts.append("no fresh backup (older than 7 days)")
    checks["alerts"] = alerts
    checks["ok"] = not alerts
    return checks


def main() -> int:
    checks = asyncio.run(collect())
    print(json.dumps(checks, indent=2, default=str))
    if checks["alerts"]:
        print("ALERTS:", "; ".join(checks["alerts"]))
        return 1
    print("status: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
