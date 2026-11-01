"""§7: POST /v1/campaigns — objective, target, budget ceiling + optimizer-guarded changes."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_role
from app.db import TenantScope, audit
from app.schemas import BudgetChange, CampaignCreate, SpendIn
from app.services.paymedia import evaluate_campaign

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


@router.post("/campaigns/{campaign_id}/budget")
async def change_budget(campaign_id: str, body: BudgetChange,
                        principal=Depends(require_role("marketer"))):
    """Optimizer-guarded budget change (§10): increases are refused unless the
    recommend-only optimizer would permit scale (sample size, CAC vs first-sale,
    margin). Reductions/safe changes always pass."""
    try:
        cid = UUID(campaign_id)
    except ValueError:
        raise HTTPException(422, "invalid campaign id")
    async with TenantScope(principal.tenant_id) as conn:
        c = await conn.fetchrow(
            "SELECT id, name, budget_cap_minor, status FROM campaigns "
            "WHERE id = $1 AND tenant_id = $2", cid, principal.tenant_id)
        if c is None:
            raise HTTPException(404, "campaign not found")
        increase = body.budget_cap_minor > c["budget_cap_minor"]
        optimizer = "not_applied"
        if increase:
            spend = (await conn.fetchval(
                "SELECT COALESCE(SUM(spend_minor),0)::bigint FROM campaign_spend_daily "
                "WHERE tenant_id = $1 AND campaign_id = $2", principal.tenant_id, cid) or 0) / 100
            qualified = await conn.fetchval(
                "SELECT COUNT(DISTINCT contact_id) FROM opportunity_attribution "
                "WHERE tenant_id = $1 AND campaign_id = $2", principal.tenant_id, cid)
            win = await conn.fetchrow(
                "SELECT COUNT(*)::int n, COALESCE(AVG(o.actual_value),0)::numeric avgv, "
                "COALESCE(SUM(o.margin),0)::numeric m "
                "FROM opportunity_attribution a JOIN opportunities o ON o.id = a.opportunity_id "
                "WHERE a.tenant_id = $1 AND a.campaign_id = $2 AND o.stage = 'won'",
                principal.tenant_id, cid)
            rec = evaluate_campaign(spend_usd=spend, qualified_leads=qualified, wins=win["n"],
                                    first_sale_usd=win["avgv"] or None, margin_usd=win["m"],
                                    budget_cap_usd=body.budget_cap_minor / 100)
            optimizer = rec["status"]
            if optimizer != "consider_test":
                raise HTTPException(409, detail={
                    "error": "budget_increase_blocked", "reasons": rec["reasons"]})
        await conn.execute("UPDATE campaigns SET budget_cap_minor = $1 WHERE id = $2",
                           body.budget_cap_minor, cid)
        await audit(conn, principal.tenant_id, principal.actor, "api.campaign.budget",
                    "campaigns", cid, {"increased": increase, "new_minor": body.budget_cap_minor})
    return {"campaign_id": str(cid), "budget_cap_minor": body.budget_cap_minor,
            "increased": increase, "optimizer": optimizer}


@router.post("/campaigns/{campaign_id}/spend")
async def record_spend(campaign_id: str, body: SpendIn,
                       principal=Depends(require_role("marketer"))):
    """Inbound spend reconciliation (§10): providers report what they actually spent;
    idempotent per (campaign, date, provider_ref). Recording is not a spend decision —
    the optimizer governs increases."""
    try:
        cid = UUID(campaign_id)
    except ValueError:
        raise HTTPException(422, "invalid campaign id")
    async with TenantScope(principal.tenant_id) as conn:
        owner = await conn.fetchval(
            "SELECT 1 FROM campaigns WHERE id = $1 AND tenant_id = $2",
            cid, principal.tenant_id)
        if owner is None:
            raise HTTPException(404, "campaign not found")
        await conn.execute(
            "INSERT INTO campaign_spend_daily (tenant_id, campaign_id, spend_date, spend_minor, "
            "currency, provider_ref) VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (tenant_id, campaign_id, spend_date, provider_ref) "
            "DO UPDATE SET spend_minor = EXCLUDED.spend_minor",
            principal.tenant_id, cid, body.spend_date, body.spend_minor, body.currency,
            body.provider_ref)
        await audit(conn, principal.tenant_id, principal.actor, "api.campaign.spend",
                    "campaign_spend_daily", None, {"campaign_id": str(cid), "spend_minor": body.spend_minor,
                                                   "provider_ref": body.provider_ref})
    return {"campaign_id": str(cid), "spend_date": body.spend_date.isoformat(),
            "spend_minor": body.spend_minor, "provider_ref": body.provider_ref}
