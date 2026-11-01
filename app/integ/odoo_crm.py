"""Odoo CRM adapter — the HighLevel replacement (ADR-0001).

Real transport: Odoo XML-RPC (/xmlrpc/2/common + /xmlrpc/2/object) on crm.lead,
res.partner, calendar.event. External-id sync via `apex_external_ref` field.
Mock mode (APEX_ODOO_MOCK=1) is an in-memory fake used by tests/offline dev —
same interface, zero server needed, so the vertical slice runs with no keys.
"""
import asyncio
import random
import xmlrpc.client
from typing import Optional

from app.integ.base import (
    AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome,
)


class OdooCRMAdapter(ProviderAdapter):
    name = "odoo"

    def __init__(self, url: str = "", db: str = "", user: str = "", password: str = "",
                 mock: bool = True, lead_model: str = "crm.lead",
                 partner_model: str = "res.partner", external_field: str = "apex_external_ref"):
        self.url = url.rstrip("/")
        self.db = db
        self.user = user
        self.password = password
        self.mock = mock
        self.lead_model = lead_model
        self.partner_model = partner_model
        self.external_field = external_field
        self._mock_store: dict[str, dict] = {}   # external_ref -> record
        # unique-per-process base so mock ids never collide across runs (real Odoo
        # always mints fresh ids — the (tenant, crm_external_id) unique stays valid)
        self._mock_seq = random.randint(10_000_000, 99_999_999)

    # ---------------------------------------------------------------- transport
    def _common(self):
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)

    def _object(self):
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    def _authenticate_sync(self) -> int:
        uid = self._common().authenticate(self.db, self.user, self.password, {})
        if not uid:
            raise ConnectionError(f"Odoo authentication failed for user '{self.user}' db '{self.db}'")
        return uid

    async def _authenticate(self) -> int:
        return await asyncio.to_thread(self._authenticate_sync)

    def _mock_create(self, external_ref: str, payload: dict) -> dict:
        if external_ref and external_ref in self._mock_store:
            return self._mock_store[external_ref]
        self._mock_seq += 1
        record = {"id": self._mock_seq, "external_ref": external_ref,
                  "stage": "new", "payload": payload}
        if external_ref:
            self._mock_store[external_ref] = record
        return record

    # ---------------------------------------------------------------- interface
    async def health_check(self) -> HealthResult:
        if self.mock:
            return HealthResult(True, self.name, "mock CRM ready (APEX_ODOO_MOCK=1)")
        if not (self.url and self.db and self.user and self.password):
            return HealthResult(False, self.name, "Odoo URL/db/user/password not configured")
        try:
            await self._authenticate()
            return HealthResult(True, self.name, "authenticated")
        except Exception as exc:
            return HealthResult(False, self.name, f"auth failed: {exc}")

    async def capabilities(self) -> list[str]:
        return ["crm.lead", "res.partner", "calendar.event", "external_ref_sync"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        try:
            if op == "create_lead":
                data = await self._create_lead(payload)
            elif op == "create_contact":
                data = await self._create_contact(payload)
            elif op == "update_stage":
                data = await self._update_stage(payload)
            elif op == "create_appointment":
                data = await self._create_appointment(payload)
            elif op == "find_by_ref":
                data = await self._find_by_ref(payload.get("external_ref", ""))
            else:
                return AdapterResult(False, {}, warnings=[f"odoo: unknown op '{op}'"])
            return AdapterResult(True, data)
        except Exception as exc:
            return AdapterResult(False, {}, warnings=[f"odoo {op} failed: {exc}"])

    # ---------------------------------------------------------------- ops
    async def _create_lead(self, p: dict) -> dict:
        ref = p.get("external_ref", "")
        if self.mock:
            rec = self._mock_create(ref, p)
            return {"id": rec["id"], "model": self.lead_model, "external_ref": ref}
        uid = await self._authenticate()
        vals = {"name": p.get("full_name") or "APEX lead", "phone": p.get("phone") or False,
                "email_from": p.get("email") or False,
                "description": f"{p.get('message') or ''}\nsource={p.get('source')}"}
        try:
            lead_id = await asyncio.to_thread(
                self._object().execute_kw, self.db, uid, self.password, self.lead_model, "create",
                [{**vals, self.external_field: ref}])
        except Exception as exc:
            if "does not exist" in str(exc):   # custom field not installed yet — degrade cleanly
                lead_id = await asyncio.to_thread(
                    self._object().execute_kw, self.db, uid, self.password, self.lead_model, "create",
                    [vals])
            else:
                raise
        return {"id": lead_id, "model": self.lead_model, "external_ref": ref}

    async def _create_contact(self, p: dict) -> dict:
        if self.mock:
            rec = self._mock_create(p.get("external_ref", ""), p)
            return {"id": rec["id"], "model": self.partner_model, "external_ref": p.get("external_ref", "")}
        uid = await self._authenticate()
        partner_id = await asyncio.to_thread(
            self._object().execute_kw, self.db, uid, self.password, self.partner_model, "create",
            [{"name": p.get("full_name") or "APEX contact", "phone": p.get("phone") or False,
              "email": p.get("email") or False}])
        return {"id": partner_id, "model": self.partner_model, "external_ref": p.get("external_ref", "")}

    async def _update_stage(self, p: dict) -> dict:
        lead_id, stage = p.get("id"), p.get("stage")
        if self.mock:
            for rec in self._mock_store.values():
                if str(rec["id"]) == str(lead_id):
                    rec["stage"] = stage
                    return {"id": lead_id, "stage": stage}
            return {"id": lead_id, "stage": stage, "warning": "not in mock store"}
        uid = await self._authenticate()
        stage_ids = await asyncio.to_thread(
            self._object().execute_kw, self.db, uid, self.password, "crm.stage", "search",
            [[("name", "=", stage)]])
        if not stage_ids:
            return {"id": lead_id, "stage": stage, "warning": f"stage '{stage}' not found in Odoo"}
        await asyncio.to_thread(
            self._object().execute_kw, self.db, uid, self.password, self.lead_model, "write",
            [[lead_id], {"stage_id": stage_ids[0]}])
        return {"id": lead_id, "stage": stage}

    async def _create_appointment(self, p: dict) -> dict:
        if self.mock:
            rec = self._mock_create(f"appt-{p.get('contact_id')}", p)
            return {"id": rec["id"], "model": "calendar.event"}
        uid = await self._authenticate()
        event_id = await asyncio.to_thread(
            self._object().execute_kw, self.db, uid, self.password, "calendar.event", "create",
            [{"name": f"APEX consultation {p.get('contact_id')}", "start": p.get("start"),
              "stop": p.get("stop")}])
        return {"id": event_id, "model": "calendar.event"}

    async def _find_by_ref(self, external_ref: str) -> dict:
        if self.mock:
            rec = self._mock_store.get(external_ref)
            return {"found": rec is not None, "id": rec["id"] if rec else None}
        uid = await self._authenticate()
        ids = await asyncio.to_thread(
            self._object().execute_kw, self.db, uid, self.password, self.lead_model, "search",
            [[(self.external_field, "=", external_ref)]])
        return {"found": bool(ids), "id": ids[0] if ids else None}

    async def reconcile(self, external_id: str) -> ReconcileResult:
        try:
            data = await self._find_by_ref(external_id)
            return ReconcileResult(ok=True, external_id=str(data["id"]) if data["id"] else external_id,
                                   found=bool(data["found"]))
        except Exception as exc:
            return ReconcileResult(ok=False, external_id=None, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        # Odoo webhook (e.g. lead created via Odoo UI) -> echo key for replay-safe intake
        return WebhookOutcome(processed=True, event_key=str(payload.get("id") or ""))
