"""Postgres access with per-transaction tenant context (ADR-0003).

Every request opens a TenantScope: BEGIN -> SET LOCAL apex.tenant_id -> work -> COMMIT.
RLS policies read that setting; unset context fails CLOSED (zero rows).
"""
import json
import uuid
from typing import AsyncIterator, Optional

import asyncpg

from app.config import settings

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


class TenantScope:
    """Transaction-scoped connection bound to one tenant (RLS context)."""

    def __init__(self, tenant_id: Optional[uuid.UUID | str] = None):
        self.tenant_id = tenant_id
        self.conn: Optional[asyncpg.Connection] = None
        self._tx = None
        self._pool: Optional[asyncpg.Pool] = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._pool = await get_pool()
        self.conn = await self._pool.acquire()
        self._tx = self.conn.transaction()
        await self._tx.start()
        if self.tenant_id is not None:
            await self.conn.execute("SELECT set_config('apex.tenant_id', $1, true)", str(self.tenant_id))
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._tx is not None:
            if exc_type is None:
                await self._tx.commit()
            else:
                await self._tx.rollback()
        if self.conn is not None and self._pool is not None:
            await self._pool.release(self.conn)


async def fetch_one(tenant_id, query: str, *args) -> Optional[asyncpg.Record]:
    async with TenantScope(tenant_id) as conn:
        return await conn.fetchrow(query, *args)


async def fetch_all(tenant_id, query: str, *args) -> list[asyncpg.Record]:
    async with TenantScope(tenant_id) as conn:
        return list(await conn.fetch(query, *args))


async def execute(tenant_id, query: str, *args) -> str:
    async with TenantScope(tenant_id) as conn:
        return await conn.execute(query, *args)


async def audit(conn, tenant_id, actor: str, action: str, entity_type: str, entity_id=None, metadata=None) -> None:
    await conn.execute(
        "INSERT INTO audit_events (tenant_id, actor, action, entity_type, entity_id, metadata) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        tenant_id, actor, action, entity_type, entity_id, json.dumps(metadata or {}))
