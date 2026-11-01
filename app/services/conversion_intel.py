"""Conversion Intelligence & Creative Evolution Engine (§4/§14 blueprint).

The agent system that FILTERS OUT bad funnels/pitches/copy and keeps only what is
likely to convert. It:
  1. campaign_hunter        — discovers ads/funnels/videos/copy (public research or a
                              deterministic mock corpus; never fabricated volumes).
  2. evidence_auditor       — grades evidence A–D (A=verified winner, D=unsubstantiated)
                              from real provable evidence, not hype. Reverse-engineers a
                              funnel's journey from its creative + offer + CTA features.
  3. copywriting_intelligence — analyzes headline/hook/emotional triggers/offers/objections/CTA.
  4. creative_evolution     — turns Grade-A/B principles + the client's brand facts into
                              ORIGINAL campaign variants (never copies script/asset).
  5. ab_test_designer       — plans a controlled A/B test across qualified leads/sales.
  6. winning_strategy_memory — records verified winners as reusable per-industry playbooks.

Honesty contract (matches the rest of APEX):
  - No fabricated conversion rates/volumes. Evidence grading is driven ONLY by what the
    caller supplies (first_party/controlled_test = higher grade, unsubstantiated = D).
  - All output creative is explicitly a hypothesis to test, never an established winner.
  - Public research is preferred; confidential_evidence is only ever the caller's own data.
"""
import hashlib
from typing import Optional

# --- Deterministic evidence grading (no hype) ------------------------------------------

GRADE_ORDER = ["A", "B", "C", "D"]


def grade_evidence(sample_size: Optional[int], methodology: str,
                   has_quantified_result: bool) -> tuple[str, str]:
    """Return (grade, rationale). Only provable inputs raise the grade."""
    if methodology in ("first_party", "controlled_test"):
        if sample_size is not None and sample_size >= 30 and has_quantified_result:
            return "A", "first-party controlled result, n>=30, quantified"
        if has_quantified_result:
            return "B", "first-party/controlled with quantified result (smaller sample)"
        return "C", "first-party but no quantified conversion result yet"
    if methodology == "case_study":
        return "B" if has_quantified_result else "C", "credible case study (quantified)" if has_quantified_result \
            else "case study without verifiable numbers"
    if methodology == "hypothesis":
        return "C", "strong creative signal, no reliable conversion figures"
    return "D", "unsubstantiated performance claim — do not treat as a winner"


def evidence_scanner_rules() -> list[str]:
    """The 'filter out the poop' heuristic lexicon — applied over research text."""
    return [
        "guaranteed results", "100% conversion", "make you rich", "overnight success",
        "no risk at all", "unlimited income", "secret formula", "get rich quick",
        "never fail", "guaranteed roi", "instant traffic", "clickbank", "misleading",
    ]


def scan_for_hype(text: str) -> list[str]:
    """Flag marketing hype so the hunter/auditor can demote unsubstantiated stuff."""
    low = (text or "").lower()
    return [rule for rule in evidence_scanner_rules() if rule in low]


# --- Copywriting intelligence ----------------------------------------------------------

HOOK_PATTERNS = {
    "problem_agitation": ["money walking out", "losing", "problem is", "frustrating", "every missed"],
    "curiosity_gap": ["what most", "the one thing", "did you know", "secret to", "nobody tells"],
    "loss_aversion": ["before you", "never again", "stop", "afraid of", "costing you"],
    "social_proof": ["how [x] grew", "case study", "real customers", "trusted by"],
    "direct_offer": ["free demo", "book a consultation", "get started", "request quote", "try now"],
}
CTA_STYLES = {
    "booking": ["book", "schedule", "consultation", "appointment", "demo"],
    "call": ["call", "phone", "talk", "get in touch"],
    "lead_form": ["quote", "request", "get started", "estimate", "submit"],
    "content_lead": ["learn more", "download", "watch", "read", "guide"],
}


def analyze_copy(headline: str, body: str = "", cta: str = "") -> dict:
    """Return structured copywriting intelligence (headline hook, CTA, triggers, objections)."""
    text = " ".join([headline, body, cta]).lower()
    hooks = [h for h, kws in HOOK_PATTERNS.items() if any(k in text for k in kws)]
    ctas = [c for c, kws in CTA_STYLES.items() if any(k in text for k in kws)]
    triggers = []
    for trig, kws in {
        "urgency": ["now", "today", "24/7", "emergency", "limited", "immediate"],
        "fear_missed_lead": ["missed", "losing", "lost", "walking out", "no answer"],
        "money_saving": ["save", "lower", "reduce", "cost effective"],
        "convenience": ["24/7", "automatic", "always", "anytime"],
        "trust": ["licensed", "insured", "local", "verified", "review"],
    }.items():
        if any(k in text for k in kws):
            triggers.append(trig)
    return {
        "headline_hook": hooks or ["none_detected"],
        "cta_style": ctas or ["none_detected"],
        "emotional_triggers": triggers or [],
        "objections_to_handle": _possible_objections(headline, body),
    }


def _possible_objections(headline: str, body: str) -> list[str]:
    text = " ".join([headline, body]).lower()
    out = []
    if "cost" in text or "price" in text or "afford" in text or "much" in text:
        out.append("price")
    if "trust" in text or "reliab" in text or "legit" in text:
        out.append("trust")
    if "time" in text or "setup" in text or "effort" in text or "hard" in text:
        out.append("effort")
    if "contract" in text or "lock in" in text or "commit" in text:
        out.append("commitment")
    return out


# --- Funnel reverse-engineering ---------------------------------------------------------

def reverse_funnel(creative_type: str, hook: str, offer: str, cta: str,
                   landing: str = "") -> dict:
    """Map the customer journey from ad/creative through to a measurable action."""
    steps = []
    if hook:
        steps.append({"step": "attract", "element": "hook/headline",
                      "note": "first-3s / first-fold signal"})
    if creative_type in ("ad_video", "sales_video"):
        steps.append({"step": "interest", "element": "video body",
                      "note": "problem + emotional trigger + credibility"})
    if offer:
        steps.append({"step": "desire", "element": "offer/lead-magnet",
                      "note": offer})
    if landing:
        steps.append({"step": "conviction", "element": "landing page",
                      "note": "objections, proof, social evidence"})
    if cta:
        cta_style = "booking" if any(k in (cta or "").lower() for k in CTA_STYLES["booking"]) else \
                    "lead" if any(k in (cta or "").lower() for k in CTA_STYLES["lead_form"]) else \
                    "call" if any(k in (cta or "").lower() for k in CTA_STYLES["call"]) else "content"
        steps.append({"step": "action", "element": f"cta:{cta_style}", "note": cta})
    return {"journey": steps, "funnel_length": len(steps),
            "primary_action": steps[-1]["element"] if steps else "none"}


# --- Creative evolution (original client variants) -----------------------------------------

ANGLE_ORDER = ["problem_agitation", "direct_offer", "curiosity_gap", "loss_aversion", "social_proof"]


def candidate_angles(original: dict, n: int = 3) -> list[str]:
    """Detected hook angle first, then distinct alternates -> several positioned variants to test."""
    hooks = analyze_copy(original.get("headline", ""), original.get("offer_text", ""), "")["headline_hook"]
    first = [h for h in hooks if h in ANGLE_ORDER][:1]
    rest = [a for a in ANGLE_ORDER if a not in first]
    return (first + rest)[:n]


def evolve_variant(principle: dict, brand: dict, original: dict, angle: Optional[str] = None) -> dict:
    """Create an ORIGINAL campaign variant from a proven principle + the client's brand.
    Uses underlying marketing principle, NOT copied scripts/headlines/footage.
    Result is always labelled a hypothesis, never an established winner."""
    industry = brand.get("industry", "B2B services")
    service = brand.get("service", "our service")
    area = brand.get("area", "your area")
    base_type = original.get("creative_type", "ad_video")

    # Map a detected hook pattern to an original angle for THIS client.
    angle_map = {
        "problem_agitation": f"Your {industry} competitors don't have better {service} than you — "
                             "sometimes they just answer the phone before you do.",
        "loss_aversion": f"Every unanswered call could be a {service} job going to a competitor "
                         f"in {area}.",
        "curiosity_gap": f"What most {industry} businesses miss about {service} (and how it "
                         "changes who wins the job).",
        "social_proof": f"How one {industry} business closed {service} deals they used to lose "
                        "after hours.",
        "direct_offer": f"Get a free {service} walkthrough for your {industry} business in {area}.",
    }
    hooks = analyze_copy(original.get("headline", ""), original.get("offer_text", ""), "")["headline_hook"]
    angle_key = angle if angle in angle_map else next((h for h in hooks if h in angle_map), "direct_offer")

    # Derive a CTA from the original funnel (or default to a booking CTA for this client).
    funnel = reverse_funnel(base_type, original.get("hook_first_3s", ""),
                            original.get("offer_text", ""), "")
    primary = funnel.get("primary_action", "cta:booking")
    ctas = {
        "cta:booking": f"Book a free {service} demo for your {area} business",
        "cta:lead": f"Request your {area} {service} quote",
        "cta:call": f"Call now — 24/7 {area} {service} response",
        "content": f"Download the {industry} {service} guide",
    }
    cta = ctas.get(primary, f"Book a free {service} demo for your {area} business")

    return {
        "headline": angle_map[angle_key],
        "angle": angle_key,
        "cta": cta,
        "positioning_note": "original variant derived from a marketing PRINCIPLE (never copied script/asset)",
        "hypothesis": True,
        "test_status_hint": "run as an A/B experiment; wait for qualified leads + closed value",
    }


def ab_test_design(control: dict, variants: list[dict], audience: str = "",
                   min_sample: int = 30) -> dict:
    """Design a controlled A/B test across qualified outcomes. Refuses eager winners."""
    return {
        "design": "single-variable controlled test",
        "control": control.get("variant_name", "control"),
        "variants": [v.get("variant_name") for v in variants],
        "audience": audience,
        "success_metric": "qualified leads -> booked -> closed value / acquisition cost",
        "min_sample_for_winner": min_sample,
        "guardrail": "no 'winner' declared before n and cost-to-value are economically sound",
    }


def content_hash(source_url: str, headline: str) -> str:
    return hashlib.sha256(f"{source_url}|{headline}".encode("utf-8")).hexdigest()


def build_playbook(variant: dict, evidence_grade: str, industry: str, simulated: bool = False) -> dict:
    """Package an original variant + its evidence level into a reusable per-industry playbook."""
    level = "hypothesis" if simulated else \
            "verified" if evidence_grade == "A" else \
            "strong_support" if evidence_grade == "B" else \
            "hypothesis" if evidence_grade in ("C",) else "unsubstantiated"
    return {
        "name": f"{industry} [{variant.get('angle', 'angle')}] "
                f"{hashlib.sha256((variant.get('headline', '') + variant.get('cta', '')).encode()).hexdigest()[:8]}",
        "industry": industry,
        "playbook_json": {
            "principle": variant.get("positioning_note", ""),
            "headline_template": variant.get("headline", ""),
            "cta_template": variant.get("cta", ""),
        },
        "evidence_level": level,
        "rights_json": {"origin": "original — derived principle, not copied asset",
                        "simulated_source": simulated,
                        "license_status": "client-authorized template"},
    }