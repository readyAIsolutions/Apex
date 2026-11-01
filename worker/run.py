"""Queue worker: Redis BRPOP -> tenant-scoped job execution -> agent_jobs state.

Durable state lives in Postgres (ADR-0003); Redis is transport only. The worker
NEVER auto-approves human gates — publishing/sending/spending happens only via
POST /v1/actions/{id}/execute after an explicit approval.
"""
import asyncio
import json
import logging
import uuid

import redis.asyncio as aioredis

from app.config import settings
from app.db import TenantScope, close_pool, get_pool
from app.schemas import AgentJobResult
from app.services import jobs as jobs_service
from worker import handlers

log = logging.getLogger("apex.worker")


async def process_one(raw: str) -> None:
    payload = json.loads(raw)
    tenant_id = uuid.UUID(payload["tenant_id"])
    job_id = uuid.UUID(payload["job_id"])
    agent = payload.get("agent", "")

    async with TenantScope(tenant_id) as conn:
        row = await jobs_service.get_job(conn, tenant_id, job_id)
        if row is None or row["state"] != "queued":
            return  # already handled / cancelled — never clobber terminal states
        await conn.execute("UPDATE agent_jobs SET state = 'running' WHERE id = $1", job_id)

        fn = handlers.HANDLERS.get(agent)
        if fn is None:
            result = AgentJobResult(status="failed",
                                    warnings=[f"no handler for agent '{agent}'"])
            await jobs_service.finish_job(conn, tenant_id, job_id, result, "failed", 0.0,
                                          result.warnings[0])
            return
        try:
            result = await fn(conn, row, payload)
            state = "succeeded" if result.status == "succeeded" else "failed"
            await jobs_service.finish_job(conn, tenant_id, job_id, result, state,
                                          result.cost_usd,
                                          None if state == "succeeded" else "; ".join(result.warnings))
        except Exception as exc:  # terminal failure, job record preserved
            log.exception("job %s failed", job_id)
            await jobs_service.finish_job(conn, tenant_id, job_id, None, "failed", 0.0, str(exc))


async def worker_loop(stop: asyncio.Event | None = None) -> None:
    await get_pool()
    r = aioredis.from_url(settings.redis_url)
    log.info("worker up on queue %s", settings.job_queue)
    try:
        while stop is None or not stop.is_set():
            try:
                raw = await r.blpop(settings.job_queue, timeout=5)
                if raw:
                    await process_one(raw[1].decode())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker iteration error (transient)")
    finally:
        await r.aclose()
        await close_pool()


async def run_once(timeout: float = 5.0) -> int:
    """Process up to jobs currently queued (used by verification).
    Blpop server-timeout races surface as redis.exceptions.TimeoutError — treat as empty."""
    import redis.exceptions as redis_exc
    await get_pool()
    r = aioredis.from_url(settings.redis_url)
    processed = 0
    try:
        while True:
            try:
                raw = await r.blpop(settings.job_queue, timeout=timeout)
            except (TimeoutError, redis_exc.TimeoutError):
                break
            if raw is None:
                break
            await process_one(raw[1].decode())
            processed += 1
    finally:
        await r.aclose()
        await close_pool()
    return processed


def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
