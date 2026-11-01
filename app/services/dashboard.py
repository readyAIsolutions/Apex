"""Attribution + economics dashboard (§10). Honest by construction: no fabricated
attribution — labels which joins are missing as caveats."""
from datetime import datetime, timezone


async def overview(conn, tenant_id) -> dict:
    spend = await conn.fetchrow(
        "SELECT COALESCE(SUM(spend_minor), 0)::bigint AS minor FROM campaign_spend_daily "
        "WHERE tenant_id = $1", tenant_id)
    spend_minor = spend["minor"]

    leads = await conn.fetchval(
        "SELECT COUNT(DISTINCT contact_id) FROM lead_events WHERE tenant_id = $1", tenant_id)
    qualified = await conn.fetchval(
        "SELECT COUNT(DISTINCT contact_id) FROM opportunities WHERE tenant_id = $1 AND stage IN "
        "('qualified','proposal','won')", tenant_id)
    booked = await conn.fetchval(
        "SELECT COUNT(*) FROM appointments WHERE tenant_id = $1 AND status IN ('booked','attended')", tenant_id)
    wins = await conn.fetchval(
        "SELECT COUNT(*) FROM opportunities WHERE tenant_id = $1 AND stage = 'won'", tenant_id)
    revenue = await conn.fetchval(
        "SELECT COALESCE(SUM(actual_value), 0)::numeric FROM opportunities WHERE tenant_id = $1 AND stage = 'won'", tenant_id)
    margin = await conn.fetchval(
        "SELECT COALESCE(SUM(margin), 0)::numeric FROM opportunities WHERE tenant_id = $1 AND stage = 'won'", tenant_id)

    spend_usd = round(spend_minor / 100, 2)
    cac = round(spend_usd / qualified, 2) if qualified else None

    wins_by_source = [dict(r) for r in await conn.fetch(
        "SELECT source, count(*)::int AS wins FROM opportunity_attribution "
        "WHERE tenant_id = $1 GROUP BY source ORDER BY wins DESC", tenant_id)]
    wins_by_campaign = [dict(r) for r in await conn.fetch(
        "SELECT COALESCE(c.name, 'unknown') AS campaign, count(*)::int AS wins "
        "FROM opportunity_attribution a LEFT JOIN campaigns c ON c.id = a.campaign_id "
        "WHERE a.tenant_id = $1 GROUP BY c.name ORDER BY wins DESC", tenant_id)]
    referral_links = await conn.fetchval(
        "SELECT count(*) FROM publications WHERE tenant_id = $1 AND platform = 'link'",
        tenant_id)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "spend_usd": spend_usd,
        "cost_per_qualified_lead": cac,
        "qualified_leads": qualified,
        "total_leads": leads,
        "booked_consultations": booked,
        "wins": wins,
        "recognized_revenue_usd": float(revenue or 0),
        "contribution_margin_usd": float(margin or 0),
        "wins_by_source": wins_by_source,
        "wins_by_campaign": wins_by_campaign,
        "referral_links": int(referral_links or 0),
        "caveats": [
            "attribution: first-touch UTM only",
            "phone/offline lead join not yet reconciled",
            "demo seed rows are marked provider_ref='demo:...'",
        ],
        "currency": "CAD",
    }
