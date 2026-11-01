"""Brevo (email) adapter — consent-based nurturing transport (§4/§5)."""
import httpx

from app.integ.base import AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome

BREVO_API = "https://api.brevo.com/v3"


class BrevoEmailAdapter(ProviderAdapter):
    name = "brevo"

    def __init__(self, api_key: str = "", sender_email: str = ""):
        self.api_key = api_key
        self.sender_email = sender_email

    async def health_check(self) -> HealthResult:
        if not self.api_key:
            return HealthResult(False, self.name, "BREVO_API_KEY not set")
        return HealthResult(True, self.name, "key present")

    async def capabilities(self) -> list[str]:
        return ["email.send"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        if op not in ("email.send", "send:email"):
            return AdapterResult(False, {}, warnings=[f"brevo: unknown op '{op}'"])
        if not self.api_key:
            return AdapterResult(False, {}, warnings=["BREVO_API_KEY not set — send skipped"])
        to = payload.get("to")
        if not to:
            return AdapterResult(False, {}, warnings=["brevo: no recipient"])
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{BREVO_API}/smtp/email",
                    headers={"api-key": self.api_key, "accept": "application/json"},
                    json={"sender": {"email": self.sender_email or "no-reply@aimbex.example"},
                          "to": [{"email": to}], "subject": payload.get("subject", ""),
                          "htmlContent": payload.get("html", "")})
                if r.status_code not in (200, 201):
                    return AdapterResult(False, {}, warnings=[f"brevo HTTP {r.status_code}: {r.text[:200]}"])
                return AdapterResult(True, {"message_id": r.json().get("messageId")})
        except Exception as exc:
            return AdapterResult(False, {}, warnings=[f"brevo failed: {exc}"])

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=external_id, found=True)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        # delivery/bounce/suppression events -> echo id so intake can dedupe
        return WebhookOutcome(processed=True, event_key=str(payload.get("message-id") or ""))
