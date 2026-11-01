"""Deterministic dev demand source — lets Workflow A run E2E with ZERO external keys.

Clearly SIMULATED: every row carries simulated=True; no click/volume fields are ever
fabricated. Swap the 'demand.pull' provider_routes row to gsc/dataforseo when those
credentialed pulls land — same row shape, no pipeline change (guaranteed-fallback
ladder pattern).
"""
import datetime

from app.integ.base import (
    AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome,
)

_ROW_SEED = 0  # bumped once per process so evidence stays unique across runs


def _demand_rows(locale: str) -> list[dict]:
    global _ROW_SEED
    _ROW_SEED += 1
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    template_queries = [
        # (query, intent)
        ("emergency flood cleanup lloydminster", "emergency"),
        ("water damage restoration open now", "emergency"),
        ("fire damage repair urgent", "emergency"),
        ("burst pipe emergency plumber", "emergency"),
        ("24/7 restoration response", "emergency"),
        ("how much does water damage restoration cost", "price"),
        ("cost of AI receptionist for contractors", "price"),
        ("missed call recovery service pricing", "price"),
        ("restoration company quote request", "price"),
        ("mold remediation estimate", "price"),
        ("AI receptionist vs answering service", "comparison"),
        ("vonage vs google voice for contractors", "comparison"),
        ("best CRM for restoration companies", "comparison"),
        ("odoo vs highlevel for service business", "comparison"),
        ("AI chatbot vs human dispatch", "comparison"),
        ("is a restoration company licensed and insured", "trust"),
        ("reviews for local restoration contractors", "trust"),
        ("are AI receptionists reliable", "trust"),
        ("does AIMBEX protect customer data", "trust"),
        ("bonded and certified restoration firm", "trust"),
        ("book a restoration consultation", "consultation"),
        ("schedule water damage walkthrough", "consultation"),
        ("AI automation demo for contractors", "consultation"),
        ("restoration sales meeting edmonton", "consultation"),
        ("call me about missed lead recovery", "consultation"),
        ("what is mold remediation", "informational"),
        ("how does an AI receptionist work", "informational"),
        ("what is contribution margin", "informational"),
        ("after hours answering for trades", "informational"),
        ("how to stop missing business calls", "informational"),
    ]
    rows = []
    for i, (q, intent) in enumerate(template_queries):
        row = {"query": q, "intent": intent, "locale": locale,
               "source": "dev_demand", "simulated": True,
               "evidence_key": f"dev-{_ROW_SEED}-{i}", "fetched_at": now}
        # NOTE: no clicks/impressions — volumes would be fabricated.
        rows.append(row)
    return rows


class DevDemandAdapter(ProviderAdapter):
    name = "dev_demand"

    async def health_check(self) -> HealthResult:
        return HealthResult(True, self.name, "deterministic dev source (simulated data)")

    async def capabilities(self) -> list[str]:
        return ["demand.queries"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        if op != "demand.queries":
            return AdapterResult(False, {}, warnings=[f"dev_demand: unknown op '{op}'"])
        rows = _demand_rows(payload.get("locale", "CA-AB"))
        return AdapterResult(
            True, {"demand": rows, "simulated": True},
            warnings=["SIMULATED demand data — swap 'demand.pull' route to gsc/dataforseo "
                      "before any real media decision"])

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=None, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        return WebhookOutcome(processed=False)
