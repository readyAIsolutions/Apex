"""Google Analytics 4 adapter — REAL runReport intake (§4, inbound analytics).

Same service-account seam as GSC. Fails closed without credentials. Normalizes
GA4 rows into spend/event summary shape for the attribution layer.
"""
import os

from app.integ.base import (
    AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome,
)
from app.integ.google_oauth import GoogleOAuth, post_json

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


class GA4Adapter(ProviderAdapter):
    name = "ga4"

    def __init__(self, oauth: GoogleOAuth | None = None, property_id: str = "",
                 transport=None):
        self.oauth = oauth or GoogleOAuth(transport=transport)
        self.property_id = property_id or os.environ.get("GA4_PROPERTY_ID", "")

    async def health_check(self) -> HealthResult:
        if not (os.environ.get("GOOGLE_OAUTH_JSON") and self.property_id):
            return HealthResult(False, self.name, "GOOGLE_OAUTH_JSON + GA4_PROPERTY_ID required")
        return HealthResult(True, self.name, "creds + property configured")

    async def capabilities(self) -> list[str]:
        return ["analytics.event_summary", "analytics.spend_reconciled"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        if not (await self.health_check()).ok:
            return AdapterResult(False, {}, warnings=["GA4 not configured — report skipped"])
        try:
            if op in ("analytics.event_summary", "analytics.spend_reconciled"):
                return await self._run_report(payload)
            return AdapterResult(False, {}, warnings=[f"ga4: unknown op '{op}'"])
        except Exception as exc:
            return AdapterResult(False, {}, warnings=[f"ga4 {op} failed: {exc}"])

    async def _run_report(self, p: dict) -> AdapterResult:
        token = await self.oauth.access_token([GA4_SCOPE])
        body = {
            "dateRanges": [{"startDate": p.get("start_date", "30daysAgo"),
                            "endDate": p.get("end_date", "today")}],
            "dimensions": [{"name": d} for d in p.get("dimensions", ["sessionDefaultChannelGroup"])],
            "metrics": [{"name": m} for m in p.get("metrics", ["totalUsers", "sessions"])],
            "limit": int(p.get("limit", 50)),
        }
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{self.property_id}:runReport"
        resp = await post_json(url, {"Authorization": f"Bearer {token}"}, body, self.oauth._transport)
        rows = [
            {d["dimension"]: d["value"] for d in r.get("dimensionValues", [])}
            | {m["metric"]: m["value"] for m in r.get("metricValues", [])}
            for r in resp.get("rows", [])
        ]
        return AdapterResult(True, {"rows": rows, "property": self.property_id,
                                    "note": "GA4 aggregates; reconcile against CRM outcomes before claiming attribution"})

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=None, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        return WebhookOutcome(processed=False)
