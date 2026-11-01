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


def normalize_question(q: str) -> str:
    return " ".join(q.lower().split())


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
    """Workflow A core: pull demand (route 'demand.pull'), evidence-store it, cluster."""
    warnings = []
    demand = []
    if payload.get("input_refs") and any(r.startswith("demand://") for r in payload["input_refs"]):
        try:
            adapter = await provider_router.route("demand.pull", conn=conn,
                                                 tenant_id=uuid.UUID(payload["tenant_id"]))
            res = await adapter.execute("demand.queries", {"locale": payload.get("constraints", {}).get("region", "CA-AB")})
            if res.ok:
                demand = res.data.get("demand", [])
            warnings += res.warnings
        except Exception as exc:
            warnings.append(f"demand pull unavailable: {exc}")

    inserted = 0
    for d in demand:
        q = normalize_question(d["query"])
        evidence = [{"source": d.get("source"), "simulated": d.get("simulated", False),
                     "fetched_at": d.get("fetched_at"), "evidence_key": d.get("evidence_key")}]
        if d.get("clicks") is not None:          # only real provider clicks — never fabricated
            evidence[0]["clicks"] = d["clicks"]
        n = await conn.execute(
            "INSERT INTO customer_questions (tenant_id, normalized_question, intent, locale, evidence_json) "
            "VALUES ($1, $2, $3, $4, $5::jsonb) "
            "ON CONFLICT (tenant_id, normalized_question) DO NOTHING",
            payload["tenant_id"], q, classify_intent(q), d.get("locale", "CA-AB"),
            json.dumps(evidence))
        if n.startswith("INSERT"):
            inserted += int(n.split()[-1])

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
        status="succeeded",
        structured_output={"clusters": clusters, "demand_pulled": len(demand),
                           "evidence_inserted": inserted, "store_total": len(evidence)},
        source_evidence=evidence, confidence=confidence,
        warnings=warnings + ["cluster counts are evidence counts, NOT search volumes"],
        recommended_next_actions=["draft answer for highest-count cluster", "submit drafts for QA"])


def _template_draft(topic: str) -> str:
    """Deterministic fallback so the content lifecycle runs keyless (guaranteed
    output ladder). Clearly labelled SIMULATED — real LLM drafts replace it once
    OPENROUTER_API_KEY is set."""
    return (
        "TEMPLATE DRAFT (SIMULATED — replace via OPENROUTER_API_KEY when provisioned)\n\n"
        f"Topic: {topic}\n\n"
        "Headline: Never Miss a Qualified Lead Again\n\n"
        "Every missed call is a missed estimate. Restoration and contractor firms in "
        "Lloydminster and Edmonton lose work when the phone rings after hours or during "
        "the next emergency. An AI receptionist answers instantly, captures the details, "
        "and books the follow-up so no qualified lead falls through the cracks.\n\n"
        "Book a consultation to see your own call-handling economics.\n"
        "(No outcome promises and no uncited figures are made in this draft.)")


async def content_draft(conn, job, payload) -> AgentJobResult:
    """Draft via routed LLM, with a SIMULATED template fallback so the pipeline
    always produces a draft asset (free -> paid ladder; no hard keyless failure)."""
    topic = payload.get("goal") or "AI receptionist for restoration firms"
    adapter = None
    try:
        adapter = await provider_router.route("draft.fast", conn=conn,
                                             tenant_id=uuid.UUID(payload["tenant_id"]))
    except Exception:
        adapter = None

    draft = None
    simulated = False
    warnings = []
    cost = 0.0
    if adapter is not None and (await adapter.health_check()).ok:
        prompt = (f"Write a 400-word factual draft for topic: {topic}. "
                  "No guaranteed-ROI claims. Audience: restoration/contractor firms in "
                  "Lloydminster/Edmonton. Plain marketing copy, no invented statistics.")
        result = await adapter.execute("chat", {"prompt": prompt})
        cost = result.cost_usd
        if result.ok:
            draft = result.data.get("output")
            warnings += result.warnings
        else:
            warnings += result.warnings
    if not draft:
        draft = _template_draft(topic)
        simulated = True
        warnings.append("SIMULATED template draft — set OPENROUTER_API_KEY for a real LLM draft")

    asset = await conn.fetchrow(
        "INSERT INTO content_assets (tenant_id, kind, status, object_uri) "
        "VALUES ($1, 'article', 'draft', $2) RETURNING id",
        payload["tenant_id"], f"draft://template/{topic[:60]}")
    return AgentJobResult(
        status="succeeded",
        structured_output={"topic": topic, "asset_id": str(asset["id"]),
                           "draft": draft, "simulated": simulated},
        confidence=0.4 if simulated else 0.5, cost_usd=cost, warnings=warnings,
        recommended_next_actions=["run fact-check QA",
                                  f"POST /v1/assets/{asset['id']}/submit for approval"])


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


async def seo_audit(conn, job, payload) -> AgentJobResult:
    """§4 SEO Auditor: deterministic on-page audit of landing-page HTML (or a URL)."""
    from app.services.seo import audit_seo
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    html = (inp or {}).get("input", {}).get("html")
    url = (inp or {}).get("input", {}).get("url", "")
    if not html and url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(url)
                r.raise_for_status()
                html = r.text[:500_000]
        except Exception as exc:
            return AgentJobResult(
                status="not_configured",
                warnings=[f"could not fetch {url}: {exc}"],
                recommended_next_actions=["provide html directly or a reachable url"])
    res = audit_seo(html or "", url)
    return AgentJobResult(status="succeeded", structured_output=res, confidence=0.9,
                          recommended_next_actions=res["recommended_next_actions"])


async def attribution(conn, job, payload) -> AgentJobResult:
    """§10 reconcile: join closed-won opportunities to their original first-touch
    lead event (source/campaign). Confidence is honest and labelled first-touch."""
    won = await conn.fetch(
        "SELECT id, contact_id, crm_external_id FROM opportunities "
        "WHERE tenant_id = $1 AND stage = 'won'", payload["tenant_id"])
    done = unknown = 0
    for o in won:
        le = await conn.fetchrow(
            "SELECT source, campaign_id, utm_json FROM lead_events "
            "WHERE tenant_id = $1 AND contact_id = $2 ORDER BY occurred_at ASC LIMIT 1",
            payload["tenant_id"], o["contact_id"])
        if le is None:
            unknown += 1
            continue
        utm = le["utm_json"]
        await conn.execute(
            "INSERT INTO opportunity_attribution "
            "(tenant_id, opportunity_id, contact_id, source, campaign_id, utm_json, confidence) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7) "
            "ON CONFLICT (tenant_id, opportunity_id) DO UPDATE SET "
            "source=EXCLUDED.source, campaign_id=EXCLUDED.campaign_id, "
            "utm_json=EXCLUDED.utm_json, confidence=EXCLUDED.confidence",
            payload["tenant_id"], o["id"], o["contact_id"], le["source"], le["campaign_id"],
            json.dumps(utm) if isinstance(utm, dict) else utm, "first-touch")
        done += 1
    return AgentJobResult(
        status="succeeded", structured_output={"attributed": done, "unknown": unknown},
        confidence=0.9,
        warnings=["first-touch UTM only; phone/offline join pending (>= 1 unknown may hide revenue)"])


async def paid_media(conn, job, payload) -> AgentJobResult:
    """§11 P1 recommend-only optimizer: per live campaign, refuses unsafe scale-ups."""
    from app.services.paymedia import evaluate_campaign
    campaigns = await conn.fetch(
        "SELECT id, name, budget_cap_minor FROM campaigns "
        "WHERE tenant_id = $1 AND status IN ('live','approved') "
        "ORDER BY created_at DESC LIMIT 20", payload["tenant_id"])
    recs = []
    for c in campaigns:
        spend = (await conn.fetchval(
            "SELECT COALESCE(SUM(spend_minor),0)::bigint FROM campaign_spend_daily "
            "WHERE tenant_id = $1 AND campaign_id = $2", payload["tenant_id"], c["id"]) or 0) / 100
        qualified = await conn.fetchval(
            "SELECT COUNT(DISTINCT contact_id) FROM opportunity_attribution "
            "WHERE tenant_id = $1 AND campaign_id = $2", payload["tenant_id"], c["id"])
        win = await conn.fetchrow(
            "SELECT COUNT(*)::int n, COALESCE(AVG(o.actual_value),0)::numeric avgv, "
            "COALESCE(SUM(o.margin),0)::numeric m "
            "FROM opportunity_attribution a JOIN opportunities o ON o.id = a.opportunity_id "
            "WHERE a.tenant_id = $1 AND a.campaign_id = $2 AND o.stage = 'won'",
            payload["tenant_id"], c["id"])
        rec = evaluate_campaign(spend_usd=spend, qualified_leads=qualified,
                                wins=win["n"], first_sale_usd=win["avgv"] or None,
                                margin_usd=win["m"], budget_cap_usd=c["budget_cap_minor"] / 100)
        recs.append({"campaign": c["name"], **rec})
    refused = any(r["status"] == "refuse_scale" for r in recs)
    return AgentJobResult(
        status="succeeded",
        structured_output={"recommendations": recs, "any_refused_scale": refused},
        confidence=0.95,
        recommended_next_actions=(["enforce refusal — do not auto-scale"] if refused
                                   else ["run capped tests within ceilings"]))


async def content_qa(conn, job, payload) -> AgentJobResult:
    """§3 Compliance/QA: deterministic pre-approval check of draft text."""
    from app.services.content_qa import qa_content
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    text = (inp or {}).get("input", {}).get("text", "")
    if not text:
        return AgentJobResult(status="not_configured",
                              warnings=["no text provided — pass input.text"],
                              recommended_next_actions=["provide input.text"])
    res = qa_content(text)
    return AgentJobResult(status="succeeded", structured_output=res, confidence=1.0,
                          recommended_next_actions=res["recommended_next_actions"])


HANDLERS = {
    "customer_question_intelligence": research_questions,
    "content_writer": content_draft,
    "content_qa": content_qa,
    "seo_auditor": seo_audit,
    "attribution": attribution,
    "paid_media": paid_media,
    "crm_sync": crm_sync,
}
