"""Health endpoints (no auth — ops probes only)."""
from fastapi import APIRouter

from app.db import get_pool

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "apex"}


@router.get("/ready")
async def ready():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ready", "db": "up"}
    except Exception as exc:
        return {"status": "degraded", "db": f"down: {exc}"}
