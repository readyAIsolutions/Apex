"""Consent ledger (§11 P0: opt-out stops automation; CASL practice)."""
import uuid
from typing import Optional

from fastapi import HTTPException

URGENT_TERMS = ["emergency", "flood", "fire", "leak", "burst", "asap", "urgent",
                "right now", "immediately", "24/7", "24-7"]


def is_urgent(message: str) -> bool:
    """Workflow B: urgent/sensitive cases transfer to a HUMAN — no auto-booking."""
    text = (message or "").lower()
    return any(t in text for t in URGENT_TERMS)


async def record_consent(conn, tenant_id, contact_id: uuid.UUID, channel: str,
                         basis: str, status: str, proof_ref: Optional[str] = None) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO consent_events (tenant_id, contact_id, channel, basis, status, proof_ref) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
        tenant_id, contact_id, channel, basis, status, proof_ref)
    if status == "opted_out":
        await conn.execute("UPDATE contacts SET consent_status = 'opted_out' WHERE id = $1", contact_id)
    elif status == "granted" and basis != "opt_out":
        await conn.execute("UPDATE contacts SET consent_status = 'granted' WHERE id = $1", contact_id)
    return dict(row)


async def is_allowed(conn, contact_id: uuid.UUID, channel: str) -> bool:
    """Latest consent event for the channel decides. Opt-out wins (CASL)."""
    row = await conn.fetchrow(
        "SELECT status FROM consent_events WHERE contact_id = $1 AND channel = $2 "
        "ORDER BY occurred_at DESC LIMIT 1", contact_id, channel)
    return row is None or row["status"] == "granted"


async def assert_allowed(conn, contact_id: uuid.UUID, channel: str) -> None:
    if not await is_allowed(conn, contact_id, channel):
        raise HTTPException(403, f"Consent blocked for channel '{channel}' (opt-out or suppression)")
