"""§7: POST /v1/leads/events — signed inbound event intake (integration identity)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import require_role
from app.db import TenantScope, audit
from app.integ import router as provider_router
from app.schemas import LeadEventIn
from app.services import leads as leads_service

router = APIRouter(tags=["leads"])


@router.post("/leads/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_lead(event: LeadEventIn, request: Request,
                      principal=Depends(require_role("analyst"))):
    tenant_id = principal.tenant_id
    crm_adapter = None
    try:
        async with TenantScope(None) as conn:
            row = await conn.fetchrow(
                "SELECT provider FROM provider_routes WHERE tenant_id = $1 AND task_type = 'crm.sync' "
                "AND enabled LIMIT 1", tenant_id)
            if row:
                crm_adapter = provider_router.adapters.get(row["provider"])
    except Exception:
        crm_adapter = None

    async with TenantScope(tenant_id) as conn:
        outcome = await leads_service.ingest_lead(conn, tenant_id, event,
                                                  crm_adapter=crm_adapter,
                                                  actor=principal.actor)
        await audit(conn, tenant_id, principal.actor, "api.lead_event", "lead_events", None,
                    {"event_key": event.event_key})
    return outcome
