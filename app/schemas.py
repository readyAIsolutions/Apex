"""Pydantic contracts (§3 agent envelope, §7 API payloads)."""
import datetime
import uuid
from datetime import datetime as dt, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class EnvelopeConstraints(BaseModel):
    max_cost_usd: float = Field(default=0.10, ge=0)
    region: str = "CA-AB"


class AgentJobEnvelope(BaseModel):
    """Common envelope for every agent (blueprint §3)."""
    tenant_id: Optional[uuid.UUID] = None
    job_id: Optional[uuid.UUID] = None
    agent: str
    goal: str
    input_refs: list[str] = []
    input: dict[str, Any] = Field(default_factory=dict)
    constraints: EnvelopeConstraints = EnvelopeConstraints()
    approval_policy: str = "draft_only"   # draft_only | gated
    idempotency_key: str


class AgentJobResult(BaseModel):
    """Every agent returns this shape (blueprint §3)."""
    status: str                                      # succeeded | failed | blocked | not_configured
    structured_output: Any = None
    source_evidence: list[dict] = []
    confidence: float = Field(default=0.0, ge=0, le=1)
    cost_usd: float = 0.0
    warnings: list[str] = []
    recommended_next_actions: list[str] = []


class LeadEventIn(BaseModel):
    source: str                       # webform|phone|email|ad|organic|referral
    event_key: str                    # external idempotency key
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    campaign_id: Optional[uuid.UUID] = None
    utm: dict[str, str] = {}
    occurred_at: Optional[str] = None
    message: Optional[str] = None      # call note / form question / voicemail text


class ConsentRecordIn(BaseModel):
    contact_id: uuid.UUID
    channel: str
    basis: str = "explicit"
    status: str = "granted"           # granted | opted_out
    proof_ref: Optional[str] = None


class AppointmentCreate(BaseModel):
    starts_at: dt = Field(default_factory=lambda: dt.now(timezone.utc))


class ApprovalDecisionIn(BaseModel):
    decision: str                     # approve | reject


class CampaignCreate(BaseModel):
    name: str
    channel: str
    objective: str = "qualified_lead"
    budget_cap_minor: int = Field(default=0, ge=0)
    status: str = "draft"


class BudgetChange(BaseModel):
    budget_cap_minor: int = Field(ge=0)


class SpendIn(BaseModel):
    spend_date: datetime.date = Field(default_factory=datetime.date.today)
    spend_minor: int = Field(ge=0)
    currency: str = "CAD"
    provider_ref: str               # provider report id (idempotency key)

class ResearchRequest(BaseModel):
    industry: str
    locale: str = "CA-AB"
    scope: dict[str, Any] = {}
    cost_cap_usd: float = Field(default=0.20, ge=0)


class SeoAuditIn(BaseModel):
    url: Optional[str] = None
    html: Optional[str] = None


class QaContentIn(BaseModel):
    text: str


class AssetCreate(BaseModel):
    kind: str = "article"
    campaign_id: Optional[uuid.UUID] = None
    note: Optional[str] = None


class AssetSubmitIn(BaseModel):
    platform: str                     # site|google|meta|youtube|email|blog
    publish_action: str = "publish"
    text: Optional[str] = None        # final copy; QA-gated before the human gate
    force: bool = False               # editor override — human authority beats the QA advisory
    extra: dict[str, Any] = {}
