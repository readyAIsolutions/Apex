"""Google Search Console adapter — REAL search-query demand intake (§4/§5 workflow A).

Implements the actual Search Analytics query call with service-account OAuth.
Without credentials it fails CLOSED (health_check ok=False) — the slot is real,
the key unlocks it. Demand rows are normalized into the same shape the question
engine consumes.
"""
import datetime
import os

from app.integ.base import (
    AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome,
)
from app.integ.google_oauth import GoogleOAuth, post_json

GSC_API = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


class GSCAdapter(ProviderAdapter):
    name = "gsc"

    def __init__(self, oauth: GoogleOAuth | None = None, site_url: str = "",
                 transport=None):
        self.oauth = oauth or GoogleOAuth(transport=transport)
        self.site_url = site_url or os.environ.get("GSC_SITE_URL", "")

    async def health_check(self) -> HealthResult:
        if not (os.environ.get("GOOGLE_OAUTH_JSON") and self.site_url):
            return HealthResult(False, self.name,
                                "GOOGLE_OAUTH_JSON + GSC_SITE_URL required (service account + verified property)")
        return HealthResult(True, self.name, "creds + property configured")

    async def capabilities(self) -> list[str]:
        return ["demand.queries", "sites.list"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        if not (await self.health_check()).ok:
            return AdapterResult(False, {}, warnings=["GSC not configured — demand pull skipped"])
        try:
            if op == "demand.queries":
                return await self._search_analytics(payload)
            if op == "sites.list":
                return await self._sites_list()
            return AdapterResult(False, {}, warnings=[f"gsc: unknown op '{op}'"])
        except Exception as exc:
            return AdapterResult(False, {}, warnings=[f"gsc {op} failed: {exc}"])

    async def _search_analytics(self, p: dict) -> AdapterResult:
        token = await self.oauth.access_token([GSC_SCOPE])
        body = {
            "startDate": p.get("start_date", "30daysAgo"),
            "endDate": p.get("end_date", "today"),
            "dimensions": ["query"],
            "rowLimit": int(p.get("row_limit", 100)),
        }
        resp = await post_json(
            GSC_API.format(site=self.site_url),
            {"Authorization": f"Bearer {token}"}, body, self.oauth._transport)
        fetched = datetime.datetime.now(datetime.timezone.utc).isoformat()
        demand = []
        for row in resp.get("rows", []):
            keys = row.get("keys") or [""]
            demand.append({
                "query": keys[0], "clicks": row.get("clicks"),
                "impressions": row.get("impressions"), "ctr": row.get("ctr"),
                "position": row.get("position"), "source": "gsc",
                "fetched_at": fetched, "simulated": False,
            })
        return AdapterResult(True, {"demand": demand,
                                    "note": f"rows={len(demand)}; volumes are GSC-click/impression evidence"})

    async def _sites_list(self) -> AdapterResult:
        token = await self.oauth.access_token([GSC_SCOPE])
        url = "https://searchconsole.googleapis.com/webmasters/v3/sites"
        if self.oauth._transport:
            resp = await self.oauth._transport(url, {"Authorization": f"Bearer {token}"}, {})
        else:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                resp = r.json()
        return AdapterResult(True, {"sites": [s.get("siteUrl") for s in resp.get("siteEntry", [])]})

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=None, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        return WebhookOutcome(processed=False)
