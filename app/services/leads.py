"""Lead pipeline (workflow B, §5): webhook -> dedupe -> consent gate -> CRM sync -> booking.

APEX is event truth; Odoo mirroring happens through the crm adapter via provider_routes.
"""
import hashlib
import json
import uuid
from typing import Optional

from app.db import audit
from app.schemas import LeadEventIn


def _email_hash(email: Optional[str]) -> Optional[str]:
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest() if email else None


def _phone_hash(phone: Optional[str]) -> Optional[str]:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return hashlib.sha256(digits.encode()).hexdigest() if digits else None


async def find_contact(conn, tenant_id, email: Optional[str], phone: Optional[str]):
    eh, ph = _email_hash(email), _phone_hash(phone)
    if eh:
        return await conn.fetchrow("SELECT * FROM contacts WHERE tenant_id = $1 AND email_hash = $2",
                                   tenant_id, eh)
    if ph:
        return await conn.fetchrow("SELECT * FROM contacts WHERE tenant_id = $1 AND phone_hash = $2",
                                   tenant_id, ph)
    return None


async def upsert_contact(conn, tenant_id, event: LeadEventIn):
    eh, ph = _email_hash(event.email), _phone_hash(event.phone)
    existing = await find_contact(conn, tenant_id, event.email, event.phone)
    if existing:
        return existing
    row = await conn.fetchrow(
        "INSERT INTO contacts (tenant_id, full_name, email_hash, phone_hash, consent_status) "
        "VALUES ($1, $2, $3, $4, 'unknown') "
        "ON CONFLICT DO NOTHING RETURNING *", tenant_id, event.full_name, eh, ph)
    if row is None:  # race: someone else inserted it
        return await find_contact(conn, tenant_id, event.email, event.phone)
    return row


async def ingest_lead(conn, tenant_id, event: LeadEventIn, crm_adapter=None,
                      actor: str = "provider:webhook") -> dict:
    """Idempotent lead intake. Returns outcome; never raises on duplicates."""
    # 1) idempotency: same (tenant, event_key) already applied? lead_events unique key.
    dup = await conn.fetchrow("SELECT id FROM lead_events WHERE tenant_id = $1 AND event_key = $2",
                              tenant_id, event.event_key)
    if dup:
        return {"processed": False, "duplicate": True, "contact_id": None, "crm_external_id": None}

    # 2) contact dedupe by email/phone hash
    contact = await upsert_contact(conn, tenant_id, event)
    contact_id = contact["id"]

    # 3) consent gate — opted_out stops everything downstream (CASL)
    if contact["consent_status"] == "opted_out":
        await audit(conn, tenant_id, actor, "lead.blocked.consent", "lead_events", None,
                    {"event_key": event.event_key})
        return {"processed": True, "blocked": "consent", "contact_id": str(contact_id),
                "crm_external_id": None}

    # 4) lead event (first-touch preserved)
    await conn.execute(
        "INSERT INTO lead_events (tenant_id, contact_id, source, campaign_id, utm_json, event_key) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        tenant_id, contact_id, event.source, event.campaign_id,
        json.dumps(event.utm), event.event_key)

    # 5) opportunity shell
    opp = await conn.fetchrow(
        "INSERT INTO opportunities (tenant_id, contact_id, stage) VALUES ($1, $2, 'new') RETURNING id",
        tenant_id, contact_id)

    # 6) CRM mirror via adapter (Odoo default; health-checked, fail-closed)
    crm_external_id = None
    if crm_adapter is not None:
        result = await crm_adapter.execute("create_lead", {
            "full_name": event.full_name, "email": event.email, "phone": event.phone,
            "source": event.source, "message": event.message, "external_ref": event.event_key,
        })
        if result.ok and result.data.get("id"):
            crm_external_id = str(result.data["id"])
            await conn.execute("UPDATE contacts SET crm_external_id = $1 WHERE id = $2",
                               crm_external_id, contact_id)
            await conn.execute("UPDATE opportunities SET crm_external_id = $1 WHERE id = $2",
                               crm_external_id, opp["id"])
        else:
            await audit(conn, tenant_id, actor, "lead.crm_sync_failed", "opportunities", opp["id"],
                        {"reason": result.warnings})

    await audit(conn, tenant_id, actor, "lead.ingested", "lead_events", None,
                {"event_key": event.event_key, "source": event.source})
    return {"processed": True, "duplicate": False, "contact_id": str(contact_id),
            "crm_external_id": crm_external_id}


async def schedule_appointment(conn, tenant_id, contact_id: uuid.UUID, opportunity_id: uuid.UUID,
                               starts_at, crm_adapter=None) -> dict:
    ext = None
    if crm_adapter is not None:
        r = await crm_adapter.execute("create_appointment", {
            "contact_id": str(contact_id), "start": starts_at.isoformat()})
        if r.ok and r.data.get("id"):
            ext = str(r.data["id"])
    row = await conn.fetchrow(
        "INSERT INTO appointments (tenant_id, contact_id, opportunity_id, starts_at, calendar_external_id) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING *",
        tenant_id, contact_id, opportunity_id, starts_at, ext)
    await audit(conn, tenant_id, "system", "appointment.booked", "appointments", row["id"], {})
    return dict(row)
