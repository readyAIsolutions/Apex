"""§7: POST /v1/actions/{id}/execute — dedicated executor runs the EXACT approved payload.

The 'action' IS the approval: immutable payload_hash, expiry, execute-once.
Executing dispatches through the provider router to the adapter implementing the
action_type (e.g. publish:site -> cms adapter; email.send -> brevo; spend -> ads adapter).
"""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_role
from app.db import TenantScope, audit
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

        # Publication receipts belong to APEX (system of record): once the vendor-side
        # op applied, record the receipt + flip the asset to published in the SAME tx.
        if task_type.startswith("publish:") and result.get("execution", {}).get("applied"):
            platform = task_type.split(":", 1)[1]
            payload = approval["payload"]
            if isinstance(payload, str):      # asyncpg returns jsonb as text
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            asset_id = (payload or {}).get("asset_id")
            execution = result.get("execution") or {}
            external_id = execution.get("external_id") or f"local:{asset_id}"
            if asset_id:
                await conn.execute(
                    "INSERT INTO publications (tenant_id, asset_id, platform, external_id, published_at, utm_json) "
                    "VALUES ($1, $2, $3, $4, now(), '{}')",
                    principal.tenant_id, asset_id, platform, str(external_id))
                await conn.execute(
                    "UPDATE content_assets SET status = 'published' "
                    "WHERE id = $1 AND tenant_id = $2", asset_id, principal.tenant_id)
                await audit(conn, principal.tenant_id, principal.actor, "asset.published",
                            "publications", None, {"platform": platform})
                result["receipt"] = {"asset_id": str(asset_id), "platform": platform,
                                     "external_id": str(external_id)}
    return result
