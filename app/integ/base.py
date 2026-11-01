"""Provider adapter contract (ADR-0002, blueprint §4).

Every external service implements THIS interface. Business logic never imports a
vendor SDK — swap software by adding an adapter + registry entry + route row.

Adapter interface (per blueprint §4):
  health_check()   -> HealthResult          capability probe
  capabilities()   -> list[str]             what this provider can do
  get_usage_cost() -> dict                  reported cost telemetry
  execute(op, payload) -> AdapterResult     do something (approved ops only)
  reconcile(external_id) -> ReconcileResult match APEX id <-> vendor id
  handle_webhook(payload) -> WebhookOutcome inbound event intake
"""
import os
import shutil
from abc import ABC
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HealthResult:
    ok: bool
    provider: str
    detail: str


@dataclass
class AdapterResult:
    ok: bool
    data: dict
    cost_usd: float = 0.0
    warnings: list = field(default_factory=list)


@dataclass
class ReconcileResult:
    ok: bool
    external_id: Optional[str]
    found: bool


@dataclass
class WebhookOutcome:
    processed: bool
    event_key: str = ""


class ProviderAdapter(ABC):
    name: str = "base"

    async def health_check(self) -> HealthResult:
        raise NotImplementedError

    async def capabilities(self) -> list[str]:
        return []

    async def get_usage_cost(self) -> dict:
        return {"provider": self.name, "cost_usd": 0.0, "currency": "USD", "detail": "metered externally"}

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        raise NotImplementedError

    async def reconcile(self, external_id: str) -> ReconcileResult:
        raise NotImplementedError

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        raise NotImplementedError


class ShellAdapter(ProviderAdapter):
    """Honest not-yet-wired slot: validates config presence, FAILS CLOSED, and
    documents the exact vendor call so wiring it later is mechanical.
    This is deliberate (no fake integrations): the slot exists, the vendor call
    lands when the credential is provided + the call is implemented."""

    def __init__(self, name: str, env_keys: list[str], endpoint_doc: str = "",
                 binary: str = ""):
        self.name = name
        self.env_keys = env_keys
        self.endpoint_doc = endpoint_doc
        self.binary = binary

    async def health_check(self) -> HealthResult:
        if self.binary and not shutil.which(self.binary):
            return HealthResult(False, self.name, f"binary '{self.binary}' not installed")
        missing = [k for k in self.env_keys if not os.environ.get(k)]
        if missing:
            return HealthResult(False, self.name, f"missing env: {', '.join(missing)}")
        return HealthResult(True, self.name, "configured (shell adapter)")

    async def capabilities(self) -> list[str]:
        return [f"{self.name}:shell"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        return AdapterResult(
            ok=False, data={}, warnings=[
                f"{self.name}: adapter shell — implement {self.endpoint_doc or 'the vendor API call'} "
                f"for op '{op}' when credentials are provisioned"])

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=external_id, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        return WebhookOutcome(processed=False)


class LocalExecutorAdapter(ProviderAdapter):
    """No-op executor for local/test ops (e.g. approval-gate verification)."""

    name = "local"

    async def health_check(self) -> HealthResult:
        return HealthResult(True, self.name, "always available")

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        return AdapterResult(ok=True, data={"op": op, "applied": True, "payload": payload})


class RouteUnavailable(Exception):
    pass
