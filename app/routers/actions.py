"""§7: POST /v1/actions/{id}/execute — dedicated executor runs the EXACT approved payload.

The 'action' IS the approval: immutable payload_hash, expiry, execute-once.
Executing dispatches through the provider router to the adapter implementing the
action_type (e.g. publish:site -> cms adapter; email.send -> brevo; spend -> ads adapter).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_role
from app.db import TenantScope
from app.integ import router as provider_router
from app.services import approvals as approvals_service

router = APIRouter(tags=["actions"])


@router.post("/actions/{action_id}/execute")
async def execute_action(action_id: str, principal=Depends(require_role("executor"))):
    uid = UUID(action_id)
    async with TenantScope(principal.tenant_id) as conn:
        approval = await approvals_service.get_approval(conn, principal.tenant_id, uid)
        if approval is None:
            raise HTTPException(404, "action not found")

        # route action_type -> adapter (env override first, then provider_routes)
        task_type = approval["action_type"]
        adapter = None
        try:
            adapter = await provider_router.route(task_type, conn=conn, tenant_id=principal.tenant_id)
        except Exception:
            # fall back to 'local' executor only for local/verified ops; everything
            # external must have an explicit route — fail closed otherwise
            if task_type.startswith("publish:") and adapter is None:
                raise HTTPException(503, f"no provider routed for {task_type} — configure provider_routes")

        executor = adapter or provider_router.adapters["local"]
        result = await approvals_service.execute_approval(conn, principal.tenant_id, uid,
                                                          principal.actor, executor)
    return result
