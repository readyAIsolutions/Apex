"""§7: POST /v1/webhooks/{provider} — verify signature, enqueue, replay-safe.

Pilot: single-tenant secret per provider (WEBHOOK_SECRET_<PROVIDER>); X-Tenant-Id
optional. Multi-tenant per-tenancy secrets arrive with P3 (secret manager lookup by
provider_connections.secret_ref).
"""
import hashlib
import hmac
import json
import uuid

from fastapi import APIRouter, Header, Request

from app.config import settings
from app.db import TenantScope, audit
from app.schemas import LeadEventIn
from app.services import leads as leads_service

router = APIRouter(tags=["webhooks"])

DEFAULT_TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/{provider}")
async def webhook(provider: str, request: Request,
                  x_tenant_id: str | None = Header(default=None),
                  x_apex_signature: str | None = Header(default=None)):
    raw = await request.body()
    secret = settings.webhook_secret(provider)
    if not secret:
        return {"error": f"no webhook secret configured for provider '{provider}'"}
    if not _verify_signature(secret, raw, x_apex_signature):
        return {"error": "invalid signature"}

    tenant_id = uuid.UUID(x_tenant_id) if x_tenant_id else DEFAULT_TENANT
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"_raw": raw.decode(errors="replace")}

    event_key = str(payload.get("event_key") or payload.get("id") or
                    hashlib.sha256(raw).hexdigest()[:32])

    # webhook_events unique(tenant, provider, event_key) = idempotent intake
    async with TenantScope(tenant_id) as conn:
        inserted = await conn.fetchrow(
            "INSERT INTO webhook_events (tenant_id, provider, event_key, payload) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (tenant_id, provider, event_key) DO NOTHING "
            "RETURNING id", tenant_id, provider, event_key, json.dumps(payload))
        if inserted is None:
            return {"processed": False, "duplicate": True, "event_key": event_key}

        # shape provider payloads into the lead envelope (form/phone/email/odoo/ads)
        source_map = {"form": "webform", "phone": "phone", "email": "email",
                      "odoo": "referral", "google_ads": "ad", "meta": "ad"}
        source = source_map.get(provider, "referral")
        event = LeadEventIn(
            source=source, event_key=event_key,
            full_name=payload.get("full_name") or payload.get("name"),
            email=payload.get("email"), phone=payload.get("phone"),
            message=payload.get("message") or payload.get("body") or payload.get("transcript"),
            utm={k: str(v) for k, v in (payload.get("utm") or {}).items()},
        )
        outcome = await leads_service.ingest_lead(conn, tenant_id, event,
                                                  actor=f"provider:{provider}")
        await conn.execute("UPDATE webhook_events SET processed_at = now() WHERE id = $1",
                           inserted["id"])
        await audit(conn, tenant_id, f"provider:{provider}", "webhook.processed",
                    "webhook_events", inserted["id"], {"source": source})
    return {"processed": True, "duplicate": False, "event_key": event_key, **outcome}
