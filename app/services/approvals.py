"""Approval state machine (§7: approval invalid if payload changes or expires).

pipeline: submitted -> pending approval -> approved|rejected|expired -> executed (once).
payload_hash is sha256 of canonical JSON; execute re-verifies hash against the stored
payload so a tampered row can never execute.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from app.config import settings
from app.db import audit

APPROVAL_TTL = timedelta(days=settings.approval_ttl_days)


def canonical_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_approval(conn, tenant_id, action_type: str, payload: dict,
                          expires_at: Optional[datetime] = None) -> dict:
    expires_at = expires_at or (datetime.now(timezone.utc) + APPROVAL_TTL)
    row = await conn.fetchrow(
        "INSERT INTO approvals (tenant_id, action_type, payload, payload_hash, expires_at) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING *",
        tenant_id, action_type, json.dumps(payload), canonical_hash(payload), expires_at)
    await audit(conn, tenant_id, "system", "approval.created", "approvals", row["id"],
                {"action_type": action_type})
    return dict(row)


async def get_approval(conn, tenant_id, approval_id: uuid.UUID, lock: bool = False) -> Optional[dict]:
    """lock=True takes a row lock (FOR UPDATE) so concurrent decide/execute serialize —
    the second caller then sees the first caller's committed state, never a stale 'approved'."""
    row = await conn.fetchrow(
        "SELECT * FROM approvals WHERE id = $1 AND tenant_id = $2" + (" FOR UPDATE" if lock else ""),
        approval_id, tenant_id)
    return dict(row) if row else None


async def decide(conn, tenant_id, approval_id: uuid.UUID, decision: str, actor: str) -> dict:
    approval = await get_approval(conn, tenant_id, approval_id, lock=True)
    if approval is None:
        raise HTTPException(404, "Approval not found")
    if approval["decision"] != "pending":
        raise HTTPException(409, f"Approval already {approval['decision']}")
    if approval["expires_at"] < datetime.now(timezone.utc):
        await conn.execute("UPDATE approvals SET decision = 'expired' WHERE id = $1", approval_id)
        raise HTTPException(410, "Approval expired")
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    new_state = "approved" if decision == "approve" else "rejected"
    row = await conn.fetchrow(
        "UPDATE approvals SET decision = $1, approver_id = NULL, decided_at = now() "
        "WHERE id = $2 RETURNING *", new_state, approval_id)
    await audit(conn, tenant_id, actor, "approval." + new_state, "approvals", approval_id,
                {"action_type": approval["action_type"]})
    return dict(row)


async def execute_approval(conn, tenant_id, approval_id: uuid.UUID, actor: str,
                           executor) -> dict:
    """Run the adapter op ONLY if approved + hash intact + not expired + not executed."""
    approval = await get_approval(conn, tenant_id, approval_id, lock=True)
    if approval is None:
        raise HTTPException(404, "Approval not found")
    if approval["decision"] == "executed":        # terminal state wins over expiry (audit integrity)
        return {"idempotent": True, **approval}
    if approval["expires_at"] < datetime.now(timezone.utc):
        await conn.execute("UPDATE approvals SET decision = 'expired' WHERE id = $1", approval_id)
        raise HTTPException(410, "Approval expired")
    if approval["decision"] != "approved":
        raise HTTPException(403, f"Not approved (state={approval['decision']})")
    stored = approval["payload"]
    if isinstance(stored, str):           # asyncpg returns jsonb as text
        try:
            stored = json.loads(stored)
        except ValueError:
            raise HTTPException(403, "Approval payload corrupt") from None
    if canonical_hash(stored) != approval["payload_hash"]:
        raise HTTPException(403, "Payload hash mismatch — approval invalidated (tamper detected)")

    result = await executor.execute(approval["action_type"], stored)
    row = await conn.fetchrow(
        "UPDATE approvals SET decision = 'executed', executed_at = now() WHERE id = $1 RETURNING *",
        approval_id)
    await audit(conn, tenant_id, actor, "approval.executed", "approvals", approval_id,
                {"action_type": approval["action_type"], "result": result.data if result else {}})
    return {"idempotent": False, **dict(row), "execution": result.data if result else {}}
