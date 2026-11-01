"""§11 P1 Paid Media Optimizer — recommend-only endpoint (marketer+)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_role
from app.db import TenantScope
from app.services.paymedia import evaluate_campaign

router = APIRouter(tags=["ads"])


@router.get("/ads/optimizer/recommend")
async def recommend(campaign_id: UUID = Query(...),
                    principal=Depends(require_role("marketer"))):
    """Recommendation for a single campaign: spend, CAC, wins and an honest scale verdict.
    Refuses unsafe increases (tiny sample / CAC past ceiling / budget breach)."""
    async with TenantScope(principal.tenant_id) as conn:
        c = await conn.fetchrow(
            "SELECT id, name, budget_cap_minor FROM campaigns WHERE id = $1 AND tenant_id = $2",
            campaign_id, principal.tenant_id)
        if c is None:
            raise HTTPException(404, "campaign not found")
        spend = (await conn.fetchval(
            "SELECT COALESCE(SUM(spend_minor),0)::bigint FROM campaign_spend_daily "
            "WHERE tenant_id = $1 AND campaign_id = $2", principal.tenant_id, campaign_id) or 0) / 100
        qualified = await conn.fetchval(
            "SELECT COUNT(DISTINCT contact_id) FROM opportunity_attribution "
            "WHERE tenant_id = $1 AND campaign_id = $2", principal.tenant_id, campaign_id)
        win = await conn.fetchrow(
            "SELECT COUNT(*)::int n, COALESCE(AVG(o.actual_value),0)::numeric avgv, "
            "COALESCE(SUM(o.margin),0)::numeric m "
            "FROM opportunity_attribution a JOIN opportunities o ON o.id = a.opportunity_id "
            "WHERE a.tenant_id = $1 AND a.campaign_id = $2 AND o.stage = 'won'",
            principal.tenant_id, campaign_id)
    rec = evaluate_campaign(spend_usd=spend, qualified_leads=qualified, wins=win["n"],
                            first_sale_usd=win["avgv"] or None, margin_usd=win["m"],
                            budget_cap_usd=c["budget_cap_minor"] / 100)
    return {"campaign": c["name"], **rec}