"""§7: POST /v1/approvals/{id}/decision — designated approver. GET queue listing."""
from fastapi import APIRouter, Depends, Query

from app.auth import require_role
from app.db import TenantScope
from app.schemas import ApprovalDecisionIn
from app.services import approvals as approvals_service

router = APIRouter(tags=["approvals"])


@router.get("/approvals")
async def list_approvals(state: str = Query("pending"),
                         principal=Depends(require_role("approver"))):
    """Operator queue: what's awaiting a human decision (publish/send/spend gates)."""
    async with TenantScope(principal.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, action_type, decision, expires_at, created_at FROM approvals "
            "WHERE tenant_id = $1 AND decision = $2 ORDER BY created_at DESC LIMIT 100",
            principal.tenant_id, state)
    return [{"id": str(r["id"]), "action_type": r["action_type"], "decision": r["decision"],
             "expires_at": r["expires_at"].isoformat(),
             "created_at": r["created_at"].isoformat()} for r in rows]


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
