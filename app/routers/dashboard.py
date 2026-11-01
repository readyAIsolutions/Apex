"""§7: GET /v1/dashboard/overview — spend, qualified leads, CAC, attribution caveats."""
from fastapi import APIRouter, Depends

from app.auth import require_role
from app.db import TenantScope
from app.services import dashboard as dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/overview")
async def overview(principal=Depends(require_role("analyst"))):
    async with TenantScope(principal.tenant_id) as conn:
        return await dashboard_service.overview(conn, principal.tenant_id)
