"""§7: POST /v1/leads/events — signed inbound event intake (integration identity)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import require_role
from app.db import TenantScope, audit
from app.integ import router as provider_router
from app.schemas import AppointmentCreate, LeadEventIn
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


@router.post("/leads/{contact_id}/appointments", status_code=status.HTTP_201_CREATED)
async def book_appointment(contact_id: str, body: AppointmentCreate,
                           principal=Depends(require_role("editor"))):
    """Workflow B (§5): offer/lock an approved consultation slot (Odoo calendar mirror)."""
    try:
        cid = UUID(contact_id)
    except ValueError:
        raise HTTPException(422, "invalid contact id")
    async with TenantScope(principal.tenant_id) as conn:
        opp = await conn.fetchrow(
            "SELECT id FROM opportunities WHERE tenant_id = $1 AND contact_id = $2 "
            "AND stage IN ('new','qualified') ORDER BY created_at DESC LIMIT 1",
            principal.tenant_id, cid)
        if opp is None:
            raise HTTPException(404, "no open opportunity for this contact")
        adapter = None
        try:
            adapter = await provider_router.route("crm.booking", conn=conn,
                                                tenant_id=principal.tenant_id)
        except Exception:
            adapter = None
        row = await leads_service.schedule_appointment(conn, principal.tenant_id, cid,
                                                       opp["id"], body.starts_at,
                                                       crm_adapter=adapter)
        await audit(conn, principal.tenant_id, principal.actor, "api.appointment.booked",
                    "appointments", row["id"], {})
    return {
        "appointment_id": str(row["id"]), "starts_at": row["starts_at"].isoformat(),
        "status": row["status"], "calendar_external_id": row["calendar_external_id"],
    }
