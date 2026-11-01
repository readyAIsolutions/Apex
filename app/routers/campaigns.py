"""§7: POST /v1/campaigns — objective, target, budget ceiling (marketer+)."""
from fastapi import APIRouter, Depends, status

from app.auth import require_role
from app.db import TenantScope, audit
from app.schemas import CampaignCreate

router = APIRouter(tags=["campaigns"])


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(c: CampaignCreate, principal=Depends(require_role("marketer"))):
    async with TenantScope(principal.tenant_id) as conn:
        row = await conn.fetchrow(
            "INSERT INTO campaigns (tenant_id, name, channel, objective, budget_cap_minor, status) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
            principal.tenant_id, c.name, c.channel, c.objective, c.budget_cap_minor, c.status)
        await audit(conn, principal.tenant_id, principal.actor, "api.campaign.created",
                    "campaigns", row["id"], {"budget_cap_minor": c.budget_cap_minor})
        return dict(row)


@router.get("/campaigns")
async def list_campaigns(principal=Depends(require_role("analyst"))):
    from app.db import fetch_all
    rows = await fetch_all(principal.tenant_id,
                           "SELECT id, name, channel, objective, budget_cap_minor, status, created_at "
                           "FROM campaigns ORDER BY created_at DESC")
    return [dict(r) for r in rows]
