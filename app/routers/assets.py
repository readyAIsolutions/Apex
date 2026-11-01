"""§7: POST /v1/assets/{id}/submit — freeze a draft for approval (editor+)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_role
from app.db import TenantScope
from app.schemas import AssetCreate, AssetSubmitIn
from app.services import approvals as approvals_service
from app.services.content_qa import qa_content

router = APIRouter(tags=["assets"])


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def create_asset(body: AssetCreate, principal=Depends(require_role("editor"))):
    """Create a draft content asset (drafts are internal until submitted for approval)."""
    async with TenantScope(principal.tenant_id) as conn:
        row = await conn.fetchrow(
            "INSERT INTO content_assets (tenant_id, kind, campaign_id, status) "
            "VALUES ($1, $2, $3, 'draft') RETURNING id, kind, status",
            principal.tenant_id, body.kind, body.campaign_id)
    return {"asset_id": str(row["id"]), "kind": row["kind"], "status": row["status"]}


@router.post("/assets/{asset_id}/submit", status_code=status.HTTP_201_CREATED)
async def submit_asset(asset_id: str, body: AssetSubmitIn,
                      principal=Depends(require_role("editor"))):
    try:
        uid = UUID(asset_id)
    except ValueError:
        raise HTTPException(422, "invalid asset id")
    async with TenantScope(principal.tenant_id) as conn:
        asset = await conn.fetchrow("SELECT * FROM content_assets WHERE id = $1 AND tenant_id = $2",
                                    uid, principal.tenant_id)
        if asset is None:
            raise HTTPException(404, "asset not found")
        if asset["status"] != "draft":
            raise HTTPException(409, f"asset already {asset['status']}")
        # Compliance/QA precondition (§3 §11): banned copy blocks submit unless forced.
        qa = None
        if body.text:
            qa = qa_content(body.text)
            failures = [f for f in qa["findings"] if f["level"] == "fail"]
            if failures and not body.force:
                raise HTTPException(409, detail={
                    "error": "qa_failed", "force_required": True,
                    "findings": failures})
        payload = {"asset_id": str(uid), "kind": asset["kind"], "platform": body.platform,
                   "publish_action": body.publish_action, "force": body.force, **body.extra}
        approval = await approvals_service.create_approval(
            conn, principal.tenant_id, f"publish:{body.platform}", payload)
        await conn.execute("UPDATE content_assets SET status = 'submitted' WHERE id = $1", uid)
        return {"asset_id": str(uid), "approval_id": str(approval["id"]),
                "expires_at": approval["expires_at"].isoformat(),
                "qa": {"pass": qa["pass"] if qa else True,
                       "findings_count": len(qa["findings"]) if qa else 0}}
