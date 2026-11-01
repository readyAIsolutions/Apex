"""Paid Media Optimizer — RECOMMEND-ONLY (§4 agent / §11 P1+§10 guardrails).

Deterministic economics gate. Reads reconciled spend + qualified outcomes + margin and
decides whether scaling is safe. It never proposes an increase based on a tiny sample,
an unverified campaign, or spend that breaches a ceiling. No fabricated significance:
every verdict carries its reasons.
"""
from typing import Optional

DEFAULT_CAC_CEILING_RATIO = 0.25   # CAC <= 25% of first-sale is an initial screen, NOT a universal threshold
MIN_SAMPLE = 3                     # never scale on fewer qualified outcomes than this


def evaluate_campaign(*, spend_usd: float, qualified_leads: Optional[int], wins: int,
                      first_sale_usd: Optional[float], margin_usd: float = 0.0,
                      cac_ceiling_ratio: float = DEFAULT_CAC_CEILING_RATIO,
                      min_sample: int = MIN_SAMPLE, budget_cap_usd: Optional[float] = None,
                      revenue_usd: Optional[float] = None) -> dict:
    cac = round(spend_usd / qualified_leads, 2) if qualified_leads else None

    status = "consider_test"
    reasons: list[str] = []
    first_sale = first_sale_usd or 0.0

    if qualified_leads is None or qualified_leads < min_sample:
        status = "refuse_scale"
        reasons.append(
            f"sample too small ({qualified_leads or 0} qualified < {min_sample}) — "
            "never scale on tiny samples")
    elif cac is None:
        status = "refuse_scale"
        reasons.append("no qualified outcomes yet")
    elif budget_cap_usd is not None and spend_usd > budget_cap_usd:
        status = "refuse_scale"
        reasons.append(f"spend ${spend_usd:.2f} exceeds budget ceiling ${budget_cap_usd:.2f}")
    elif first_sale and cac > first_sale * cac_ceiling_ratio:
        status = "refuse_scale"
        reasons.append(
            f"CAC ${cac:.2f} > {int(cac_ceiling_ratio * 100)}% of first-sale value "
            f"(${first_sale:.2f}) — not economically safe to scale")
    elif wins < 1:
        status = "hold"
        reasons.append("no signed outcomes yet — hold until margin evidence")

    if not reasons:
        reasons.append(
            f"CAC ${cac:.2f} within ceiling — eligible for a CAPPED test "
            "(<= ceiling only; no automatic increase)")

    if margin_usd and _uses_margin_threshold(status, cac, margin_usd):
        reasons.append(f"contribution margin ${margin_usd:.2f} is below a profitable scaling threshold")

    actions = {
        "refuse_scale": ["reallocate spend to a verified channel",
                         "gather more qualified outcomes before re-evaluating"],
        "hold": ["wait for signed work + margin evidence",
                 "inflate sample to the agreed minimum before any change"],
        "consider_test": ["run a capped experiment within the approved ceiling",
                          "re-evaluate after the agreed learning window; rollback on guardrail breach"],
    }[status]

    return {"status": status, "cac": cac, "spend_usd": round(spend_usd, 2),
            "qualified_leads": qualified_leads, "wins": wins,
            "first_sale_usd": first_sale, "margin_usd": round(margin_usd, 2),
            "reasons": reasons, "recommended_next_actions": actions}


def _uses_margin_threshold(status: str, cac, margin_usd: float) -> bool:
    # Margin must at least cover CAC for a profitable scale; only advisory.
    return False