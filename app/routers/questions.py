"""§7: POST /v1/questions/research — async question-intelligence job (editor+)."""

from fastapi import APIRouter, Depends, status

from app.auth import require_role
from app.db import TenantScope, audit
from app.schemas import AgentJobEnvelope, ResearchRequest
from app.services import jobs as jobs_service

router = APIRouter(tags=["questions"])


@router.post("/questions/research", status_code=status.HTTP_202_ACCEPTED)
async def research(req: ResearchRequest, principal=Depends(require_role("editor"))):
    envelope = AgentJobEnvelope(
        agent="customer_question_intelligence",
        goal=f"Find verified high-intent customer questions for {req.industry} in {req.locale}",
        input_refs=[f"scope://{req.industry}?locale={req.locale}"],
        constraints={"max_cost_usd": req.cost_cap_usd, "region": req.locale},
        idempotency_key=f"research:{req.industry}:{req.locale}",
    )
    async with TenantScope(principal.tenant_id) as conn:
        from app.config import settings
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        try:
            result = await jobs_service.create_job(conn, principal.tenant_id, envelope, redis=r)
        finally:
            await r.aclose()
        await audit(conn, principal.tenant_id, principal.actor, "api.questions.research",
                    "agent_jobs", None, {"industry": req.industry})
    return result
