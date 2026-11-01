"""§7: POST /v1/approvals/{id}/decision — designated approver."""
from fastapi import APIRouter, Depends

from app.auth import require_role
from app.db import TenantScope
from app.schemas import ApprovalDecisionIn
from app.services import approvals as approvals_service

router = APIRouter(tags=["approvals"])


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: str, body: ApprovalDecisionIn,
                 principal=Depends(require_role("approver"))):
    from uuid import UUID
    uid = UUID(approval_id)
    async with TenantScope(principal.tenant_id) as conn:
        result = await approvals_service.decide(conn, principal.tenant_id, uid,
                                                body.decision, principal.actor)
    return {"approval_id": approval_id, "decision": result["decision"],
            "decided_at": result["decided_at"].isoformat() if result["decided_at"] else None}
