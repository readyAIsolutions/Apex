"""Bearer API-key auth + RBAC (§7: strict server-side RBAC, request IDs).

Pilot keys live in api_keys (sha256 at rest). Roles: analyst < editor < marketer <
orchestrator < approver / executor < admin. The key's tenant is authoritative —
no client-supplied tenant claim.
"""
import hashlib
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app.db import TenantScope

ROLE_RANK = {
    "analyst": 1, "editor": 2, "marketer": 3, "orchestrator": 4,
    "approver": 5, "executor": 5, "admin": 99,
}


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class Principal:
    tenant_id: uuid.UUID
    name: str
    roles: list[str]

    @property
    def actor(self) -> str:
        return f"api_key:{self.name}"

    def has_role(self, min_role: str) -> bool:
        rank = ROLE_RANK.get(min_role, 99)
        return any(ROLE_RANK.get(r, -1) >= rank for r in self.roles) or "admin" in self.roles


def bearer_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer API key")
    return authorization.removeprefix("Bearer ").strip()


async def resolve_principal(token: str) -> Principal:
    h = hash_key(token)
    async with TenantScope(None) as conn:
        row = await conn.fetchrow(
            "SELECT k.tenant_id, k.name, k.roles, k.expires_at, t.status AS tenant_status "
            "FROM api_keys k JOIN tenants t ON t.id = k.tenant_id WHERE k.key_hash = $1", h)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    if row["expires_at"] is not None and row["expires_at"] < __import__("datetime").datetime.now(__import__("datetime").timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired")
    if row["tenant_status"] != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tenant not active")
    return Principal(tenant_id=row["tenant_id"], name=row["name"], roles=list(row["roles"]))


def require_role(min_role: str):
    """Sync factory returning an async dependency — call at import: require_role('executor')."""
    async def dep(request: Request, token: str = Depends(bearer_token)) -> Principal:
        principal = await resolve_principal(token)
        if not principal.has_role(min_role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role >= {min_role}")
        request.state.principal = principal
        request.state.tenant_id = principal.tenant_id
        return principal
    return dep
