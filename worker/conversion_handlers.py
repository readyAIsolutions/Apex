"""Conversion Intelligence & Creative Evolution Engine — worker agents (§4/§14).

Seven specialist agents operating over campaign_research / conversion_evidence /
creative_experiments / conversion_playbooks (migration 0015):

  campaign_hunter          pull campaign/copy research via 'research.pull' route
  evidence_auditor         grade each campaign A–D from provable evidence; demote hype (D)
  copywriting_intelligence analyze headline/hook/triggers/objections/CTA
  funnel_reverse_engineer  map ad→landing→CTA journey for a campaign
  creative_evolution       derive ORIGINAL client variants from A/B principles
  ab_test_designer         design a controlled A/B test (refuses eager winners)
  winning_strategy_memory  package verified winners into per-industry playbooks

honesty contract: no fabricated conversion rates/volumes; research source is the
provider route, and every original variant is explicitly a hypothesis to test.
"""
import datetime
import json
import uuid

from app.integ import router as provider_router
from app.schemas import AgentJobResult
from app.services import approvals as approvals_service
from app.services import conversion_intel as ci

# Persistent agents: these also write to the DB tables; transient analyzers just compute.
WRITES_TO_DB = ("campaign_hunter", "evidence_auditor", "creative_evolution", "winning_strategy_memory")


async def _insert_research(conn, tenant_id, row: dict) -> str:
    """Insert one observed campaign + its highest-confidence creative features."""
    features = ci.analyze_copy(row.get("headline", ""), row.get("body", ""), row.get("cta_text", ""))
    fid = await conn.fetchval(
        "INSERT INTO campaign_research (tenant_id, provider, source_url, creative_type, headline, "
        "hook_first_3s, offer_text, claims, copy_features, observed_signals, confidential_evidence, "
        "collected_by) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,$12) RETURNING id",
        tenant_id, row.get("source", "campaign_corpus"), row.get("source_url", "n/a"),
        row.get("creative_type", "ad_video"),
        (row.get("headline") or "")[:400],
        (row.get("hook_first_3s") or (row.get("headline") or "")[:60]),
        (row.get("offer_text") or "")[:400],
        (row.get("body") or "")[:800],
        json.dumps(features), json.dumps({}), bool(row.get("confidential_evidence", False)),
        "campaign_hunter")
    return fid


def _grade_row(row: dict, hype: list[str]) -> tuple[str, str]:
    """Grade a research row A–D using only provable signals; hype forces demotion."""
    methodology = row.get("methodology", "unsubstantiated")
    sample = row.get("sample_size")
    quantified = bool(row.get("quantified_result"))
    grade, rationale = ci.grade_evidence(sample, methodology, quantified)
    if hype and grade in ("A", "B"):
        grade, rationale = "D", "demoted: marketing-hype language detected (" + ", ".join(hype) + ")"
    elif hype and grade == "C":
        grade, rationale = "D", "hype language detected — unsubstantiated (" + ", ".join(hype) + ")"
    return grade, rationale


async def campaign_hunter(conn, job, payload) -> AgentJobResult:
    tenant = payload.get("tenant_id") or job.get("tenant_id")
    rows = payload.get("research")  # optional explicit rows
    simulated = bool(payload.get("research") is not None)
    if rows is None:
        adapter = await provider_router.route("research.pull", conn=conn, tenant_id=tenant)
        res = await adapter.execute("research.pull", {"region": payload.get("region", "CA-AB")})
        if not res.ok:
            return AgentJobResult(status="failed", structured_output={"error": res.warnings})
        rows = res.data.get("research", [])
        simulated = bool(res.data.get("simulated", False))
    stored, grades = [], {}
    for row in rows:
        hype = ci.scan_for_hype(" ".join([row.get("headline", ""), row.get("body", ""), row.get("offer_text", "")]))
        grade, rationale = _grade_row(row, hype)
        rid = await _insert_research(conn, tenant, row)
        existing = await conn.fetchval(
            "SELECT id FROM conversion_evidence WHERE tenant_id=$1 AND research_id=$2",
            tenant, rid)
        if existing:
            await conn.execute("UPDATE conversion_evidence SET grade=$3 WHERE id=$1",
                               existing, rid, grade)
        else:
            await conn.execute(
                "INSERT INTO conversion_evidence (tenant_id, research_id, grade, claimed_metrics, "
                "verified_metrics, sample_size, methodology, confidence, provenance) "
                "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9)",
                tenant, rid, grade, json.dumps({}), json.dumps({}), row.get("sample_size"),
                row.get("methodology", "hypothesis"),
                "low" if grade in ("C", "D") else "medium", rationale)
        grades[grade] = grades.get(grade, 0) + 1
        stored.append({"research_id": str(rid), "grade": grade,
                       "headline": (row.get("headline") or "")[:60]})
    return AgentJobResult(
        status="succeeded",
        structured_output={"stored": stored, "grade_totals": grades,
                           "filtered_out": grades.get("D", 0),
                           "simulated": simulated},
        recommended_next_actions=[
            f"evidence_auditor: score the {grades.get('B',0)+grades.get('A',0)} strong rows next",
            "creative_evolution: derive original variants from grade A/B principles"])


async def evidence_auditor(conn, job, payload) -> AgentJobResult:
    tenant = payload.get("tenant_id") or job.get("tenant_id")
    scored = []
    rows = await conn.fetch(
        "SELECT r.id, r.headline, r.offer_text, r.creative_type, e.grade, e.methodology, e.sample_size "
        "FROM conversion_evidence e JOIN campaign_research r ON r.id = e.research_id "
        "WHERE e.tenant_id = $1 ORDER BY e.grade ASC, e.sample_size DESC NULLS LAST", tenant)
    for r in rows:
        hype = ci.scan_for_hype(" ".join([r["headline"], r["offer_text"] or ""]))
        body_text = r["offer_text"] or ""
        copy = ci.analyze_copy(r["headline"] or "", body_text, "")
        funnel = ci.reverse_funnel(r["creative_type"], r["headline"] or "",
                                   r["offer_text"] or "", "")
        scored.append({"research_id": str(r["id"]), "grade": r["grade"],
                       "headline": (r["headline"] or "")[:60],
                       "copy_intel": copy, "funnel": funnel})
    return AgentJobResult(
        status="succeeded",
        structured_output={"scored": scored, "count": len(scored),
                           "keeper_grade": [s["grade"] for s in scored if s["grade"] in ("A", "B")]},
        recommended_next_actions=["creative_evolution: evolve keepers into client variants"])


async def creative_evolution(conn, job, payload) -> AgentJobResult:
    """Derive ORIGINAL variants from the strongest (A/B) evidence + this tenant's brand."""
    tenant = payload.get("tenant_id") or job.get("tenant_id")
    brand = payload.get("brand") or {
        "industry": "B2B AI automation for contractors",
        "service": "AI receptionist / missed-call recovery",
        "area": "Lloydminster & Edmonton (CA-AB)",
    }
    keepers = await conn.fetch(
        "SELECT r.* FROM conversion_evidence e JOIN campaign_research r ON r.id = e.research_id "
        "WHERE e.tenant_id = $1 AND e.grade IN ('A','B') ORDER BY e.sample_size DESC NULLS LAST LIMIT 5",
        tenant)
    variants = []
    for k in keepers:
        origin = {"creative_type": k["creative_type"], "headline": k["headline"],
                  "offer_text": k["offer_text"] or "", "hook_first_3s": k["hook_first_3s"]}
        variant = ci.evolve_variant({}, brand, origin)
        vid = await conn.fetchval(
            "INSERT INTO creative_experiments (tenant_id, variant_name, audience, variant_json, status, "
            "budget_cap_minor) VALUES ($1,$2,$3,$4::jsonb,'draft',0) RETURNING id",
            tenant, f"{brand['industry']} v{len(variants)+1}",
            payload.get("audience", "local service-area businesses"),
            json.dumps({"headline": variant["headline"], "cta": variant["cta"],
                        "positioning_note": variant["positioning_note"],
                        "hypothesis": True}))
        variants.append({"experiment_id": str(vid), "headline": variant["headline"],
                         "cta": variant["cta"]})
        # Package a reusable playbook from the principle (evidence-level from source grade).
        src_grade = k.get("grade") or "hypothesis"
        pb = ci.build_playbook(variant, src_grade, brand["industry"])
        await conn.execute(
            "INSERT INTO conversion_playbooks (tenant_id, name, industry, playbook_json, "
            "supporting_evidence_uuids, evidence_level, rights_json) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7::jsonb)",
            tenant, pb["name"], pb["industry"], json.dumps(pb["playbook_json"]),
            json.dumps([str(k["id"])]), pb["evidence_level"], json.dumps(pb["rights_json"]))
    return AgentJobResult(
        status="succeeded",
        structured_output={"variants": variants, "brand": brand,
                           "hypothesis_note": "all variants are hypotheses to test, not winners"},
        recommended_next_actions=[
            "ab_test_designer: design a controlled test across these variants",
            "content_qa + approval before any asset is built from a variant"])


async def ab_test_designer(conn, job, payload) -> AgentJobResult:
    """Design a controlled A/B test; refuses to declare a winner on a tiny sample."""
    tenant = payload.get("tenant_id") or job.get("tenant_id")
    control_name = payload.get("control", f"control-{str(uuid.uuid4())[:6]}")
    variants = payload.get("variants", [])
    design = ci.ab_test_design({"variant_name": control_name}, variants,
                               payload.get("audience", ""), int(payload.get("min_sample", 30)))
    test_id = await conn.fetchval(
        "INSERT INTO creative_experiments (tenant_id, variant_name, audience, variant_json, status) "
        "VALUES ($1,$2,$3,$4::jsonb,'running') RETURNING id",
        tenant, control_name + " (control)", payload.get("audience", ""), json.dumps(design))
    return AgentJobResult(
        status="succeeded",
        structured_output={"test_id": str(test_id), "design": design,
                           "guardrail": design["guardrail"]},
        recommended_next_actions=["run variants to a qualified-lead / closed-value endpoint",
                                  "wait for sample >= min_sample before declaring a winner"])


async def winning_strategy_memory(conn, job, payload) -> AgentJobResult:
    """Summarize verified (A) winning principles as a reusable industry playbook."""
    tenant = payload.get("tenant_id") or job.get("tenant_id")
    verified = await conn.fetch(
        "SELECT r.headline, r.offer_text, r.creative_type FROM conversion_evidence e "
        "JOIN campaign_research r ON r.id = e.research_id WHERE e.tenant_id = $1 AND e.grade = 'A'",
        tenant)
    summary = [{"headline": r["headline"], "creative_type": r["creative_type"],
                "principle": "verified first-party / controlled result"} for r in verified]
    return AgentJobResult(
        status="succeeded",
        structured_output={"verified_winners": summary, "count": len(summary),
                           "note": "generalized learning stays intra-tenant; no cross-client data leaks"},
        recommended_next_actions=["adopt winning principles into new client onboarding"])


# dispatcher table for this engine
CONVERSION_HANDLERS = {
    "campaign_hunter": campaign_hunter,
    "evidence_auditor": evidence_auditor,
    "creative_evolution": creative_evolution,
    "ab_test_designer": ab_test_designer,
    "winning_strategy_memory": winning_strategy_memory,
}


def get(agent: str):
    return CONVERSION_HANDLERS.get(agent)