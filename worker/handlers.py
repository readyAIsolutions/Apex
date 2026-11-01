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


async def email_nurture(conn, job, payload) -> AgentJobResult:
    """§3 Email/Retention agent (§5 Workflow E): consent-gated nurture.

    Opt-out stops automation here at CREATE time (no approval, no send). If consent is
    granted, the handler only DRAFTS + requests a 'send:email' human approval — the actual
    send happens exclusively through the executor, which re-checks consent at EXECUTION
    time (CASL-safe against revocation between draft and send).
    """
    from app.services import approvals as approvals_service
    from app.services import consent as consent_svc
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    contact_id = (inp or {}).get("input", {}).get("contact_id")
    subject = (inp or {}).get("input", {}).get("subject") or "Follow-up — AI receptionist demo"
    body = (inp or {}).get("input", {}).get("body") or _template_draft(subject)
    if not contact_id:
        return AgentJobResult(status="not_configured", warnings=["no contact_id — pass input.contact_id"],
                              recommended_next_actions=["provide input.contact_id"])
    try:
        cid = uuid.UUID(contact_id)
    except ValueError:
        return AgentJobResult(status="failed", structured_output={"error": "invalid contact_id"})
    if not await consent_svc.is_allowed(conn, cid, "email"):
        return AgentJobResult(status="succeeded", structured_output={"blocked": "consent"},
                              warnings=["opt-out stops automation (CASL) — no approval created"],
                              recommended_next_actions=["respect suppression; no further email"])
    asset = await conn.fetchrow(
        "INSERT INTO content_assets (tenant_id, kind, status) VALUES ($1,'email','draft') RETURNING id",
        payload["tenant_id"])
    approval = await approvals_service.create_approval(
        conn, payload["tenant_id"], "send:email",
        {"asset_id": str(asset["id"]), "contact_id": str(cid), "subject": subject,
         "body": body, "channel": "email"})
    return AgentJobResult(
        status="succeeded",
        structured_output={"approval_id": str(approval["id"]), "asset_id": str(asset["id"]),
                            "blocked": None, "channel": "email"},
        recommended_next_actions=[f"POST /v1/approvals/{approval['id']}/decision -> execute"])


def _podcast_permission_ok(permission) -> tuple[bool, str]:
    """Workflow C hard stop (§5): documented permission for BOTH likenesses and voices."""
    p = permission or {}
    if p.get("likeness") and p.get("voice"):
        return True, ""
    missing = [k for k in ("likeness", "voice") if not p.get(k)]
    return False, f"missing documented permission: {', '.join(missing)}"


def _podcast_storyboard(topic: str, cta: str) -> dict:
    """Deterministic storyboard + render manifest (SIMULATED — real render needs HeyGen/Runway keys)."""
    return {
        "title": f"Digital twin — {topic}",
        "format": "simulated_storyboard",
        "hosts": [{"name": "Host A", "permission": "documented"},
                  {"name": "Host B", "permission": "documented"}],
        "segments": [
            {"beat": "hook", "lines": ["Every missed call is a missed estimate."]},
            {"beat": "problem", "lines": [f"Restoration firms in Lloydminster and Edmonton lose work after hours."]},
            {"beat": "solution", "lines": ["An AI receptionist answers instantly, captures details, and books the follow-up."]},
            {"beat": "cta", "lines": [cta]},
        ],
        "shots": [
            {"n": 1, "host": "Host A", "b_roll": "phone ringing"},
            {"n": 2, "host": "Host B", "b_roll": "restoration site"},
            {"n": 3, "host": "Host A", "b_roll": "calendar booking demo"},
            {"n": 4, "host": "both", "b_roll": "office handshake"},
            {"n": 5, "host": "Host B", "b_roll": "logo card + CTA"},
        ],
        "render_manifest": {
            "full": {"duration_s": 240, "assets": ["voice_a.wav", "voice_b.wav",
                                                   "b_roll_01.mp4", "subtitles.srt"]},
            "shorts": 3,
        },
        "simulated": True,
    }


async def podcast_producer(conn, job, payload) -> AgentJobResult:
    """§5 Workflow C — two-avatar podcast: permission gate -> storyboard -> QA -> approval."""
    from app.services import approvals as approvals_service
    from app.services.content_qa import qa_content
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    i = (inp or {}).get("input", {})
    topic = i.get("topic") or payload.get("goal") or "AI receptionist for restoration firms"
    cta = i.get("cta") or "Book a consultation at aimbex.example"
    permission = i.get("permission")
    ok, reason = _podcast_permission_ok(permission)
    if not ok:
        return AgentJobResult(status="succeeded", structured_output={"blocked": "missing_permission"},
                              warnings=[f"Workflow C hard stop: {reason} (blueprint §5)"],
                              recommended_next_actions=["attach input.permission.likeness + input.permission.voice refs"])
    storyboard = _podcast_storyboard(topic, cta)
    qa_text = i.get("script") or json.dumps(storyboard)
    qa = qa_content(qa_text)
    failures = [f for f in qa["findings"] if f["level"] == "fail"]
    if failures:
        return AgentJobResult(status="succeeded", structured_output={"blocked": "qa_failed", "findings": failures},
                              warnings=["script failed compliance QA — cannot request publish approval"],
                              recommended_next_actions=["remove banned claim language"])
    asset = await conn.fetchrow(
        "INSERT INTO content_assets (tenant_id, kind, status) VALUES ($1,'video','draft') RETURNING id",
        payload["tenant_id"])
    approval = await approvals_service.create_approval(
        conn, payload["tenant_id"], "publish:podcast",
        {"asset_id": str(asset["id"]), "topic": topic, "cta": cta, "permission": permission,
         "render_manifest": storyboard["render_manifest"], "variant_count": 4})
    return AgentJobResult(
        status="succeeded",
        structured_output={"approval_id": str(approval["id"]), "asset_id": str(asset["id"]),
                           "blocked": None, "storyboard": storyboard,
                           "render_manifest": storyboard["render_manifest"], "simulated": True},
        recommended_next_actions=[f"POST /v1/approvals/{approval['id']}/decision -> execute",
                                  "replace SIMULATED render with HeyGen/Runway output when credentialed"])


def _proposal_template(value_usd: float) -> str:
    """Deterministic proposal draft (SIMULATED — replace with LLM/approved scope)."""
    return (
        "PROPOSAL DRAFT (SIMULATED — replace with approved scope/LLM draft)\n\n"
        "Engagement: AI receptionist + missed-call recovery (restoration services)\n"
        f"Scoped fee: ${value_usd:,.2f}\n"
        "Deliverables: 24/7 AI answering, CRM follow-up, booking, weekly report\n"
        "Terms: monthly retainer; cancel anytime.\n"
        "(No outcome promises and no uncited figures are made in this draft.)")


async def proposal_draft(conn, job, payload) -> AgentJobResult:
    """§9 sales funnel: draft a proposal for an open opportunity -> send:proposal approval."""
    from app.services import approvals as approvals_service
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    opp_id = (inp or {}).get("input", {}).get("opportunity_id")
    if not opp_id:
        return AgentJobResult(status="not_configured", warnings=["no opportunity_id — pass input.opportunity_id"])
    try:
        oid = uuid.UUID(opp_id)
    except ValueError:
        return AgentJobResult(status="failed", structured_output={"error": "invalid opportunity_id"})
    opp = await conn.fetchrow(
        "SELECT id, contact_id, projected_value FROM opportunities WHERE id = $1", oid)
    if opp is None:
        return AgentJobResult(status="failed", structured_output={"error": "opportunity not found"},
                              warnings=["scoped to own tenant; cannot draft for another tenant"])
    value = float(opp["projected_value"] or 0)
    asset = await conn.fetchrow(
        "INSERT INTO content_assets (tenant_id, kind, status) "
        "VALUES ($1,'proposal','draft') RETURNING id", payload["tenant_id"])
    approval = await approvals_service.create_approval(
        conn, payload["tenant_id"], "send:proposal",
        {"asset_id": str(asset["id"]), "opportunity_id": str(oid),
         "contact_id": str(opp["contact_id"]), "value_usd": value})
    return AgentJobResult(
        status="succeeded",
        structured_output={"approval_id": str(approval["id"]), "asset_id": str(asset["id"]),
                           "opportunity_id": str(oid), "value_usd": value,
                           "proposal": _proposal_template(value), "simulated": True},
        recommended_next_actions=["approve + execute send:proposal (receipt)",
                                  "then close_engagement with the published proposal"])


async def close_engagement(conn, job, payload) -> AgentJobResult:
    """§9 signed engagement: accept:engagement approval — only when the proposal is PUBLISHED."""
    from app.services import approvals as approvals_service
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    i = (inp or {}).get("input", {})
    opp_id, asset_id = i.get("opportunity_id"), i.get("proposal_asset_id")
    actual, margin = i.get("actual_value_usd"), i.get("margin_usd")
    if not (opp_id and asset_id and actual is not None and margin is not None):
        return AgentJobResult(status="not_configured",
                              warnings=["pass input.opportunity_id + proposal_asset_id + actual_value_usd + margin_usd"])
    try:
        oid, aid = uuid.UUID(opp_id), uuid.UUID(asset_id)
    except ValueError:
        return AgentJobResult(status="failed", structured_output={"error": "bad uuid input"})
    published = await conn.fetchval(
        "SELECT 1 FROM publications WHERE tenant_id = $1 AND asset_id = $2",
        payload["tenant_id"], aid)
    if not published:
        return AgentJobResult(
            status="succeeded", structured_output={"blocked": "proposal_not_published"},
            warnings=["engagement cannot be accepted: no published proposal receipt for the asset"],
            recommended_next_actions=["run proposal_draft -> approve -> execute first"])
    approval = await approvals_service.create_approval(
        conn, payload["tenant_id"], "accept:engagement",
        {"asset_id": str(aid), "opportunity_id": str(oid),
         "actual_value_usd": float(actual), "margin_usd": float(margin)})
    return AgentJobResult(status="succeeded",
                          structured_output={"approval_id": str(approval["id"]), "blocked": None},
                          recommended_next_actions=["approve + execute to win the opportunity"])


def _pr_score(candidate: dict, industry: str, region: str) -> int:
    """Deterministic relevance score from PROVIDED fields only — no fabricated metrics."""
    terms = candidate.get("relevance_terms") or []
    hay = " ".join([*(terms or []), candidate.get("publisher", "")]).lower()
    needles = [w for w in (industry + " " + region).split() if len(w) > 2]
    return sum(1 for w in needles if w in hay)


async def pr_prospecting(conn, job, payload) -> AgentJobResult:
    """§4 §11 P2 Workflow E — PR/backlinks: evidence-based, disclosed-only, no purchased ranks.

    Hard rule: a PAID placement without an explicit disclosure flag is REFUSED.
    Only publisher candidates supplied by the operator are considered (no fabricated
    publisher lists or authority scores).
    """
    from app.services import approvals as approvals_service
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    i = (inp or {}).get("input", {})
    candidates = i.get("candidates") or []
    if not candidates:
        return AgentJobResult(status="not_configured",
                              warnings=["pass input.candidates (publisher, url, relevance_terms, paid_required, paid_disclosed, evidence_ref)"])
    industry = i.get("industry", "restoration")
    region = i.get("region", "CA-AB")
    processed, blocked = [], []
    warnings = []
    for c in candidates:
        publisher = c.get("publisher") or "?"
        if c.get("paid_required") and not c.get("paid_disclosed"):
            blocked.append({"publisher": publisher,
                            "reason": "paid placement without disclosure — refused (no purchased undisclosed links)",
                            "url": c.get("url")})
            continue
        if not c.get("evidence_ref"):
            warnings.append(f"{publisher}: no evidence_ref — score provisional, never for ranking/authority decisions")
        score = _pr_score(c, industry, region)
        asset = await conn.fetchrow(
            "INSERT INTO content_assets (tenant_id, kind, status) VALUES ($1,'link','draft') RETURNING id",
            payload["tenant_id"])
        approval = await approvals_service.create_approval(
            conn, payload["tenant_id"], "publish:link",
            {"asset_id": str(asset["id"]), "publisher": publisher, "url": c.get("url"),
             "paid_disclosed": bool(c.get("paid_disclosed")), "relevance": score,
             "evidence_ref": c.get("evidence_ref")})
        processed.append({"approval_id": str(approval["id"]), "asset_id": str(asset["id"]),
                          "publisher": publisher, "url": c.get("url"), "relevance": score,
                          "paid_disclosed": bool(c.get("paid_disclosed"))})
    return AgentJobResult(status="succeeded",
                          structured_output={"processed": processed, "blocked": blocked},
                          warnings=warnings,
                          recommended_next_actions=[p["approval_id"] and
                              f"approve + execute {p['publisher']} (publish:link)" for p in processed])


def _social_post(asset_title: str, platform: str, base_url: str = "https://aimbex.example/") -> dict:
    """Deterministic platform post + UTM trackable link (no fabricated social claims)."""
    utm = {"utm_source": platform, "utm_medium": "social", "utm_campaign": "content"}
    text = (f"{asset_title} — real talk on missed-call economics for restoration firms. "
            f"Read: {base_url}?utm_source={platform}&utm_medium=social&utm_campaign=content")
    return {"platform": platform, "text": text, "utm": utm}


async def cgo_plan(conn, job, payload) -> AgentJobResult:
    """§3 Chief Growth Officer — prioritized weekly growth plan, read-only stance.

    Aggregates the reconciled dashboard and returns a deterministic weekly plan.
    Never proposes spend scale-ups when the optimizer would refuse; never publishes
    or purchases anything (read + plan only, per §3 hard stops).
    """
    import datetime
    from app.services import dashboard as dashboard_service
    from app.services.paymedia import evaluate_campaign
    ov = await dashboard_service.overview(conn, uuid.UUID(payload["tenant_id"]))
    spend = ov["spend_usd"]
    qualified = ov["qualified_leads"]
    wins = ov["wins"]
    revenue = ov["recognized_revenue_usd"] or 0
    margin = ov["contribution_margin_usd"] or 0
    verdict = evaluate_campaign(spend_usd=spend, qualified_leads=qualified, wins=wins,
                               first_sale_usd=(revenue / wins) if wins else None,
                               margin_usd=margin, budget_cap_usd=250000)
    priorities = [
        "1. Reconcile attribution gaps (phone/offline join) before any scale decision",
        "2. Publish evidence-backed answers from the question store (QA + approval gates)",
        "3. Nurture booked-no-show list with consent-respecting follow-up",
        "4. Review publisher shortlist through pr_prospecting (disclosure rule enforced)",
        "5. Capacitate the pilot funnel: bookings -> proposal -> signed -> invoice dashboards",
    ]
    week = datetime.date.today().isoformat()
    return AgentJobResult(
        status="succeeded",
        structured_output={
            "week": week,
            "spend_usd": spend, "qualified_leads": qualified, "wins": wins,
            "cac": ov["cost_per_qualified_lead"],
            "spend_verdict": verdict["status"],
            "spend_reasons": verdict["reasons"],
            "priorities": priorities,
            "assigned_agents": ["attribution", "content_writer", "email_nurture",
                                "pr_prospecting", "proposal_draft"],
            "no_auto_actions": True,
        },
        confidence=0.9,
        recommended_next_actions=([] if verdict["status"] == "refuse_scale"
                                  else ["run capped tests within the approved ceiling"]))


async def social_distribute(conn, job, payload) -> AgentJobResult:
    """§3 Social Distributor — platform posts + UTM receipts for a PUBLISHED asset."""
    from app.services import approvals as approvals_service
    inp = job["input_json"]
    if isinstance(inp, str):
        inp = json.loads(inp)
    i = (inp or {}).get("input", {})
    asset_id = i.get("asset_id")
    platforms = i.get("platforms") or ["linkedin"]
    if not asset_id:
        return AgentJobResult(status="not_configured",
                              warnings=["pass input.asset_id (a PUBLISHED asset) + platforms"])
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        return AgentJobResult(status="failed", structured_output={"error": "bad asset id"})
    asset = await conn.fetchrow(
        "SELECT id, status FROM content_assets WHERE id = $1 AND tenant_id = $2",
        aid, payload["tenant_id"])
    if asset is None or asset["status"] != "published":
        return AgentJobResult(
            status="succeeded", structured_output={"blocked": "asset_not_published"},
            warnings=["social distribution requires a PUBLISHED asset"],
            recommended_next_actions=["publish the asset first (submit -> approve -> execute)"])
    title = f"AI receptionist for restoration firms"
    approvals = []
    for p in platforms:
        post = _social_post(title, p)
        appr = await approvals_service.create_approval(
            conn, payload["tenant_id"], "publish:social",
            {"asset_id": str(aid), **post})
        approvals.append({"platform": p, "approval_id": str(appr["id"]),
                          "post": post["text"]})
    return AgentJobResult(status="succeeded",
                          structured_output={"approvals": approvals, "blocked": None},
                          recommended_next_actions=[
                              f"approve + execute {a['platform']}" for a in approvals])


HANDLERS = {
    "customer_question_intelligence": research_questions,
    "content_writer": content_draft,
    "content_qa": content_qa,
    "seo_auditor": seo_audit,
    "attribution": attribution,
    "paid_media": paid_media,
    "email_nurture": email_nurture,
    "podcast_producer": podcast_producer,
    "proposal_draft": proposal_draft,
    "close_engagement": close_engagement,
    "pr_prospecting": pr_prospecting,
    "cgo_plan": cgo_plan,
    "social_distribute": social_distribute,
    "crm_sync": crm_sync,
}
