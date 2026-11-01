"""§7: POST /v1/agents/jobs (orchestrator+) + GET /v1/agents/jobs/{id} (own tenant)."""
import json

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_role
from app.config import settings
from app.db import TenantScope, audit
from app.schemas import AgentJobEnvelope
from app.services import jobs as jobs_service

router = APIRouter(tags=["agents"])


@router.post("/agents/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_agent_job(envelope: AgentJobEnvelope,
                           principal=Depends(require_role("orchestrator"))):
    import redis.asyncio as aioredis
    r = aioredis.from_url(settings.redis_url)
    try:
        async with TenantScope(principal.tenant_id) as conn:
            result = await jobs_service.create_job(conn, principal.tenant_id, envelope, redis=r)
            await audit(conn, principal.tenant_id, principal.actor, "api.agent.job.created",
                        "agent_jobs", None, {"agent": envelope.agent})
    finally:
        await r.aclose()
    return result


@router.get("/agents/jobs/{job_id}")
async def get_agent_job(job_id: str, principal=Depends(require_role("analyst"))):
    from uuid import UUID
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(422, "invalid job id")
    async with TenantScope(principal.tenant_id) as conn:
        row = await jobs_service.get_job(conn, principal.tenant_id, job_uuid)
    if row is None:
        raise HTTPException(404, "job not found")
    return dict(row)
