"""OpenRouter model gateway adapter (§4, model routing).

Model IDs are DATA (provider_routes table), never code. Cost telemetry is taken from
the usage block; per-token pricing unknown for arbitrary models -> estimated with a
warning rather than fabricated.
"""
import asyncio
from typing import Optional

import httpx

from app.integ.base import AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome

# Rough USD-per-1M-tokens (Jan 2026 ballpark, input/output) for the seeded models.
# Unknown models -> estimate 0 with a warning (reconciled from provider telemetry later).
_KNOWN_PRICING: dict[str, tuple[float, float]] = {
    "deepseek/deepseek-chat": (0.14, 0.28),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "anthropic/claude-sonnet-4": (3.00, 15.00),
}


class OpenRouterAdapter(ProviderAdapter):
    name = "openrouter"

    def __init__(self, api_key: str = "", base_url: str = "https://openrouter.ai/api/v1",
                 default_model: str = "deepseek/deepseek-chat"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    async def health_check(self) -> HealthResult:
        if not self.api_key:
            return HealthResult(False, self.name, "OPENROUTER_API_KEY not set")
        return HealthResult(True, self.name, "key present")

    async def capabilities(self) -> list[str]:
        return ["chat", "classify", "extract", "verify"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        if op not in ("chat", "classify", "extract", "verify"):
            return AdapterResult(False, {}, warnings=[f"openrouter: unknown op '{op}'"])
        if not self.api_key:
            return AdapterResult(False, {}, warnings=["OPENROUTER_API_KEY not set — draft skipped"])

        model = payload.get("model") or self.default_model
        messages = payload.get("messages") or [{"role": "user", "content": payload.get("prompt", "")}]
        json_mode = op in ("extract", "verify", "classify")

        body: dict = {"model": model, "messages": messages, "temperature": 0.2}
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "HTTP-Referer": "https://apex.aimbex.example",
                             "X-Title": "APEX COMMAND AI"},
                    json=body)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            return AdapterResult(False, {}, warnings=[f"openrouter call failed: {exc}"])

        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if content is None:
            # placeholder models ('openrouter/auto') return null content — never trust them
            return AdapterResult(False, {}, warnings=[
                "model returned empty content (placeholder route?) — use a concrete model id"])

        usage = data.get("usage") or {}
        cost = 0.0
        warnings = []
        if usage:
            in_p, out_p = _KNOWN_PRICING.get(model, (0.0, 0.0))
            cost = round((usage.get("prompt_tokens", 0) * in_p
                          + usage.get("completion_tokens", 0) * out_p) / 1_000_000, 6)
            if in_p == 0:
                warnings.append(f"cost estimate unavailable for model '{model}' (reconcile from telemetry)")
        else:
            warnings.append("no usage block returned — cost unknown")

        output = content
        if json_mode:
            import json as _json
            try:
                output = _json.loads(content)
            except Exception:
                warnings.append("json_mode requested but content not valid JSON")

        return AdapterResult(True, {"model": model, "output": output},
                             cost_usd=cost, warnings=warnings)

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=None, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        return WebhookOutcome(processed=False)
