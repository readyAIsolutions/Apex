"""§3 Compliance/QA — deterministic content gate (editor+)."""
from fastapi import APIRouter, Depends

from app.auth import require_role
from app.schemas import QaContentIn
from app.services.content_qa import qa_content

router = APIRouter(tags=["qa"])


@router.post("/qa/content")
async def qa_content_endpoint(body: QaContentIn, principal=Depends(require_role("editor"))):
    """Pre-approval check of draft copy: banned outcome promises, uncited stats, consent."""
    return qa_content(body.text)