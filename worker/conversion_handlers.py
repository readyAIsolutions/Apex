"""Conversion Intelligence & Creative Evolution Engine — worker agents (§4/§14).

campaign_hunter          pull campaign research via the 'research.pull' route, dedupe, grade A-D
evidence_auditor         read-only audit: copy intel + funnel map per stored campaign
creative_evolution       derive ORIGINAL client variants (several angles) from A/B keepers
ab_test_designer         design a controlled test (refuses eager winners)
winning_strategy_memory  summarise verified winners (simulated data is never called verified)

Honesty contract: no fabricated conversion numbers; simulated sources are flagged all the
way through to playbook evidence levels; every variant is a hypothesis.
"""
import json

from app.integ import router as provider_router
from app.schemas import AgentJobResult
from app.services import conversion_intel as ci


def job_input(job, payload=None) -> dict:
    """Envelope input lives in agent_jobs.input_json['input'] (the Redis payload carries no input)."""
    raw = job["input_json"] if job is not None else None
    if isinstance(raw, str):
        raw = json.loads(raw)
    return dict((raw or {}).get("input") or {})


def _tenant(job, payload):
    return payload.get("tenant_id") or job["tenant_id"]


def _grade_row(row: dict, hype: list[str]) -> tuple[str, str]:
    grade, why = ci.grade_evidence(row.get("sample_size"), row.get("methodology", "unsubstantiated"),
                                   bool(row.get("quantified_result")))
    if hype:
        return "D", "demoted: marketing-hype language (" + ", ".join(hype) + ")"
    return grade, why


async def campaign_hunter(conn, job, payload) -> AgentJobResult:
    tenant, inp = _tenant(job, payload), job_input(job, payload)
    rows, simulated = inp.get("research"), False
    if rows is None:
        adapter = await provider_router.route("research.pull", conn=conn, tenant_id=tenant)
        res = await adapter.execute("research.pull", {"region": inp.get("region", "CA-AB")})
        if not res.ok:
            return AgentJobResult(status="failed", warnings=res.warnings,
                                  structured_output={"error": res.warnings})
        rows, simulated = res.data.get("research", []), bool(res.data.get("simulated"))
    stored, grades, new_rows = [], {}, 0
    for row in rows:
        hype = ci.scan_for_hype(" ".join([row.get("headline", ""), row.get("body", ""),
                                          row.get("offer_text", "")]))
        grade, why = _grade_row(row, hype)
        h = ci.content_hash(row.get("source_url", "n/a"), row.get("headline", ""))
        features = ci.analyze_copy(row.get("headline", ""), row.get("body", ""), row.get("cta_text", ""))
        rec = await conn.fetchrow(
            "INSERT INTO campaign_research (tenant_id, provider, source_url, creative_type, headline, "
            "hook_first_3s, offer_text, claims, copy_features, observed_signals, confidential_evidence, "
            "collected_by, content_hash) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,$12,$13) "
            "ON CONFLICT (tenant_id, content_hash) DO UPDATE SET observed_signals = EXCLUDED.observed_signals "
            "RETURNING id, (xmax = 0) AS inserted",
            tenant, row.get("source", "campaign_corpus"), row.get("source_url", "n/a"),
            row.get("creative_type", "ad_video"), (row.get("headline") or "")[:400],
            (row.get("hook_first_3s") or (row.get("headline") or "")[:60]),
            (row.get("offer_text") or "")[:400], (row.get("body") or "")[:800],
            json.dumps(features), json.dumps({"simulated": simulated}),
            bool(row.get("confidential_evidence", False)), "campaign_hunter", h)
        rid = rec["id"]          # known rows are refreshed + re-graded in place, never duplicated
        new_rows += 1 if rec["inserted"] else 0
        await conn.execute(
            "INSERT INTO conversion_evidence (tenant_id, research_id, grade, claimed_metrics, "
            "verified_metrics, sample_size, methodology, confidence, provenance) "
            "VALUES ($1,$2,$3,'{}'::jsonb,$4::jsonb,$5,$6,$7,$8) "
            "ON CONFLICT (tenant_id, research_id) DO UPDATE SET grade = EXCLUDED.grade, "
            "provenance = EXCLUDED.provenance, verified_metrics = EXCLUDED.verified_metrics",
            tenant, rid, grade, json.dumps({"simulated": simulated}), row.get("sample_size"),
            row.get("methodology", "hypothesis"), "low" if grade in ("C", "D") else "medium",
            ("[SIMULATED SOURCE] " if simulated else "") + why)
        grades[grade] = grades.get(grade, 0) + 1
        stored.append({"research_id": str(rid), "grade": grade, "headline": (row.get("headline") or "")[:60]})
    return AgentJobResult(
        status="succeeded",
        structured_output={"stored": stored, "new_rows": new_rows, "grade_totals": grades,
                           "filtered_out": grades.get("D", 0), "simulated": simulated},
        warnings=["research source is SIMULATED — grades are demonstrations, not market proof"] if simulated else [],
        recommended_next_actions=["creative_evolution: derive original variants from A/B keepers"])


async def evidence_auditor(conn, job, payload) -> AgentJobResult:
    tenant = _tenant(job, payload)
    rows = await conn.fetch(
        "SELECT r.id, r.headline, r.offer_text, r.creative_type, e.grade FROM conversion_evidence e "
        "JOIN campaign_research r ON r.id = e.research_id WHERE e.tenant_id = $1 "
        "ORDER BY e.grade, r.headline", tenant)
    scored = [{"research_id": str(r["id"]), "grade": r["grade"], "headline": (r["headline"] or "")[:60],
               "copy_intel": ci.analyze_copy(r["headline"] or "", r["offer_text"] or "", ""),
               "funnel": ci.reverse_funnel(r["creative_type"], r["headline"] or "", r["offer_text"] or "", "")}
              for r in rows]
    return AgentJobResult(
        status="succeeded",
        structured_output={"scored": scored, "count": len(scored),
                           "keepers": [s["research_id"] for s in scored if s["grade"] in ("A", "B")]},
        recommended_next_actions=["creative_evolution: evolve keepers into client variants"])


async def creative_evolution(conn, job, payload) -> AgentJobResult:
    tenant, inp = _tenant(job, payload), job_input(job, payload)
    brand = inp.get("brand") or {"industry": "B2B AI automation for contractors",
                                 "service": "AI receptionist / missed-call recovery",
                                 "area": "Lloydminster & Edmonton (CA-AB)"}
    per_keeper = max(1, min(int(inp.get("variants_per_keeper", 3)), 5))
    keepers = await conn.fetch(
        "SELECT r.id, r.creative_type, r.headline, r.offer_text, r.hook_first_3s, r.observed_signals, "
        "e.id AS evidence_id, e.grade FROM conversion_evidence e JOIN campaign_research r ON r.id = e.research_id "
        "WHERE e.tenant_id = $1 AND e.grade IN ('A','B') ORDER BY e.grade, e.sample_size DESC NULLS LAST LIMIT 5",
        tenant)
    variants, created = [], 0
    for k in keepers:
        sig = k["observed_signals"]
        sig = json.loads(sig) if isinstance(sig, str) else (sig or {})
        simulated = bool(sig.get("simulated"))
        origin = {"creative_type": k["creative_type"], "headline": k["headline"],
                  "offer_text": k["offer_text"] or "", "hook_first_3s": k["hook_first_3s"]}
        for angle in ci.candidate_angles(origin, per_keeper):
            v = ci.evolve_variant({}, brand, origin, angle=angle)
            name = f"{brand['industry']} [{angle}] {ci.content_hash(k['headline'], v['headline'])[:8]}"
            vid = await conn.fetchval(
                "INSERT INTO creative_experiments (tenant_id, variant_name, source_evidence_id, audience, "
                "variant_json, status) SELECT $1,$2,$3,$4,$5::jsonb,'draft' WHERE NOT EXISTS "
                "(SELECT 1 FROM creative_experiments WHERE tenant_id=$1 AND variant_name=$2) RETURNING id",
                tenant, name, k["evidence_id"], inp.get("audience", "local service-area businesses"),
                json.dumps({"headline": v["headline"], "cta": v["cta"], "angle": angle,
                            "positioning_note": v["positioning_note"], "hypothesis": True}))
            if vid is not None:
                created += 1
            pb = ci.build_playbook(v, k["grade"], brand["industry"], simulated=simulated)
            await conn.execute(
                "INSERT INTO conversion_playbooks (tenant_id, name, industry, playbook_json, "
                "supporting_evidence_uuids, evidence_level, rights_json) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7::jsonb) "
                "ON CONFLICT (tenant_id, name) DO UPDATE SET evidence_level = EXCLUDED.evidence_level, "
                "rights_json = EXCLUDED.rights_json, supporting_evidence_uuids = EXCLUDED.supporting_evidence_uuids",
                tenant, pb["name"], pb["industry"], json.dumps(pb["playbook_json"]),
                json.dumps([str(k["id"])]), pb["evidence_level"], json.dumps(pb["rights_json"]))
            variants.append({"experiment_id": str(vid) if vid else None, "angle": angle,
                             "headline": v["headline"], "cta": v["cta"], "new": vid is not None})
    return AgentJobResult(
        status="succeeded",
        structured_output={"variants": variants, "created": created, "brand": brand,
                           "hypothesis_note": "all variants are hypotheses to test, not winners"},
        warnings=[] if keepers else ["no A/B keepers yet — run campaign_hunter first"],
        recommended_next_actions=["ab_test_designer: test these variants head-to-head",
                                  "content_qa + approval before any asset is built from a variant"])


async def ab_test_designer(conn, job, payload) -> AgentJobResult:
    tenant, inp = _tenant(job, payload), job_input(job, payload)
    known = {r["variant_name"] for r in await conn.fetch(
        "SELECT variant_name FROM creative_experiments WHERE tenant_id=$1 AND status IN ('draft','queued')",
        tenant)}
    names = [n for n in (inp.get("variants") or []) if isinstance(n, str) and n in known]
    if len(names) < 2:     # only real, stored variants can be tested
        names = [r["variant_name"] for r in await conn.fetch(
            "SELECT variant_name FROM creative_experiments WHERE tenant_id=$1 AND status IN ('draft','queued') "
            "ORDER BY created_at DESC, variant_name LIMIT 4", tenant)]
    variants = [{"variant_name": n if isinstance(n, str) else n.get("variant_name", "?")} for n in names]
    if len(variants) < 2:
        return AgentJobResult(status="succeeded", structured_output={"blocked": "need_two_variants"},
                              warnings=["an A/B test needs at least 2 variants"])
    control = variants[0]
    design = ci.ab_test_design(control, variants[1:], inp.get("audience", ""), int(inp.get("min_sample", 30)))
    queued = []
    for v in variants:
        await conn.execute(
            "UPDATE creative_experiments SET status='queued' WHERE tenant_id=$1 AND variant_name=$2 "
            "AND status='draft'", tenant, v["variant_name"])
        queued.append(v["variant_name"])
    return AgentJobResult(
        status="succeeded",
        structured_output={"design": design, "queued": queued, "guardrail": design["guardrail"]},
        recommended_next_actions=["launch only after content_qa + approval; spend needs a budget cap",
                                  "no winner before min_sample qualified leads AND closed value"])


async def winning_strategy_memory(conn, job, payload) -> AgentJobResult:
    tenant = _tenant(job, payload)
    rows = await conn.fetch(
        "SELECT r.headline, r.creative_type, r.observed_signals FROM conversion_evidence e "
        "JOIN campaign_research r ON r.id = e.research_id WHERE e.tenant_id=$1 AND e.grade='A'", tenant)
    verified, simulated = [], []
    for r in rows:
        sig = r["observed_signals"]
        sig = json.loads(sig) if isinstance(sig, str) else (sig or {})
        (simulated if sig.get("simulated") else verified).append(
            {"headline": r["headline"], "creative_type": r["creative_type"]})
    return AgentJobResult(
        status="succeeded",
        structured_output={"verified_winners": verified, "simulated_examples": simulated,
                           "count": len(verified),
                           "note": "simulated rows are demonstrations, never counted as verified winners"},
        recommended_next_actions=["adopt verified principles into client onboarding"])


CONVERSION_HANDLERS = {
    "campaign_hunter": campaign_hunter, "evidence_auditor": evidence_auditor,
    "creative_evolution": creative_evolution, "ab_test_designer": ab_test_designer,
    "winning_strategy_memory": winning_strategy_memory,
}
