"""Agent handlers — deterministic-first, evidence-only (blueprint §3/§5).

No fabricated numbers: search volumes/shapes only appear when a real data source
provided them; otherwise the result carries a warning.
"""
import json
import uuid

from app.integ import router as provider_router
from app.schemas import AgentJobResult

INTENT_KEYWORDS = {
    "emergency": ["emergency", "immediate", "burst", "flood", "fire", "leak", "24/7", "asap", "now"],
    "price": ["cost", "price", "quote", "estimate", "how much", "fee", "expensive", "budget"],
    "comparison": ["vs", "versus", "better", "compare", "alternative", "difference", "or "],
    "trust": ["license", "insured", "certified", "bonded", "reliable", "review", "verify", "reputable"],
    "consultation": ["consult", "meet", "call me", "book", "demo", "walkthrough", "appointment"],
}


def classify_intent(question: str) -> str:
    q = question.lower()
    for intent in ("emergency", "price", "comparison", "trust", "consultation"):
        if any(k in q for k in INTENT_KEYWORDS[intent]):
            return intent
    return "informational"


def cluster_by_intent(questions: list[str]) -> list[dict]:
    buckets: dict[str, list[str]] = {}
    for q in questions:
        q = " ".join(q.lower().split())
        buckets.setdefault(classify_intent(q), []).append(q)
    return [{"intent": intent, "questions": list(dict.fromkeys(qs))[:20], "count": len(set(qs))}
            for intent, qs in sorted(buckets.items())]


async def research_questions(conn, job, payload) -> AgentJobResult:
    rows = await conn.fetch(
        "SELECT normalized_question, intent, evidence_json, locale FROM customer_questions "
        "WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 500", payload["tenant_id"])
    evidence = []
    for r in rows:
        ev = r["evidence_json"]
        evidence.append({"question": r["normalized_question"], "intent": r["intent"],
                         "locale": r["locale"],
                         "sources": json.loads(ev) if isinstance(ev, str) else ev})
    if not evidence:
        return AgentJobResult(
            status="succeeded", structured_output={"clusters": []}, confidence=0.0,
            warnings=["no question evidence in store yet — connect GSC/DataForSEO adapters"],
            recommended_next_actions=["wire gsc adapter", "run workflow A (weekly demand research)"])
    clusters = cluster_by_intent([e["question"] for e in evidence])
    confidence = round(min(0.9, 0.5 + 0.08 * len(evidence)), 2)
    return AgentJobResult(
        status="succeeded", structured_output={"clusters": clusters},
        source_evidence=evidence, confidence=confidence,
        warnings=["cluster counts are evidence counts, NOT search volumes"],
        recommended_next_actions=["draft answer for highest-count cluster", "submit drafts for QA"])


async def content_draft(conn, job, payload) -> AgentJobResult:
    topic = payload.get("goal") or "AI receptionist for restoration firms"
    adapter = None
    try:
        adapter = await provider_router.route("draft.fast", conn=conn,
                                             tenant_id=uuid.UUID(payload["tenant_id"]))
    except Exception:
        adapter = None
    if adapter is None:
        return AgentJobResult(
            status="not_configured", structured_output=None,
            warnings=["no provider routed for draft.fast"],
            recommended_next_actions=["set OPENROUTER_API_KEY", "add provider_routes row draft.fast"])
    prompt = (f"Write a 400-word factual draft for topic: {topic}. "
              "No guaranteed-ROI claims. Audience: restoration/contractor firms in "
              "Lloydminster/Edmonton. Plain marketing copy, no invented statistics.")
    result = await adapter.execute("chat", {"prompt": prompt})
    if not result.ok:
        return AgentJobResult(status="failed", warnings=result.warnings, cost_usd=result.cost_usd)
    return AgentJobResult(
        status="succeeded",
        structured_output={"topic": topic, "draft": result.data.get("output")},
        confidence=0.5, cost_usd=result.cost_usd, warnings=result.warnings,
        recommended_next_actions=["run fact-check QA", "submit asset for approval"])


async def crm_sync(conn, job, payload) -> AgentJobResult:
    odoo = provider_router.adapters.get("odoo")
    if odoo is None:
        return AgentJobResult(status="not_configured", warnings=["odoo adapter missing"])
    contacts = await conn.fetch(
        "SELECT id, full_name FROM contacts WHERE tenant_id = $1 AND crm_external_id IS NULL LIMIT 50",
        payload["tenant_id"])
    created = 0
    for c in contacts:
        ref = f"apex-{c['id']}"
        r = await odoo.execute("create_lead", {"full_name": c["full_name"], "external_ref": ref,
                                              "message": "created by crm_sync job"})
        if r.ok and r.data.get("id"):
            await conn.execute("UPDATE contacts SET crm_external_id = $1 WHERE id = $2",
                               str(r.data["id"]), c["id"])
            created += 1
    return AgentJobResult(status="succeeded",
                          structured_output={"synced_contacts": created, "sync_started": True},
                          confidence=1.0)


HANDLERS = {
    "customer_question_intelligence": research_questions,
    "content_writer": content_draft,
    "crm_sync": crm_sync,
}
