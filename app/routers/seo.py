"""§4 SEO Auditor — synchronous on-page audit (deterministic, keyless)."""
from fastapi import APIRouter, Depends

from app.auth import require_role
from app.schemas import SeoAuditIn
from app.services.seo import audit_seo

router = APIRouter(tags=["seo"])


@router.post("/seo/audit")
async def seo_audit_sync(body: SeoAuditIn, principal=Depends(require_role("editor"))):
    return audit_seo(body.html or "", body.url or "")