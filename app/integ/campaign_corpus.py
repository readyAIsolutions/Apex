"""Deterministic campaign/research corpus — lets the Conversion Intelligence engine run E2E
with ZERO external keys. Clearly SIMULATED: every row carries simulated=true; no conversion
rates/volumes are ever fabricated. Swap the 'research.pull' provider_routes row to
meta_ads_library / semrush / similarweb when credentialed pulls land — same shape, no
pipeline change (guaranteed-fallback ladder, ADR-0002).

Includes deliberately-divergent examples so the Evidence Auditor can DEMOTE hype (bad
funnels/copy) to grade D and KEEP only provable, well-structured creative at A/B/C.
"""
import datetime
import uuid
from typing import Optional

from app.integ.base import (
    AdapterResult, HealthResult, ProviderAdapter, ReconcileResult, WebhookOutcome,
)

_CORPUS_SEED = 0


def _evidence_variant(seed_suffix: int, headline: str, body: str, offer: str, cta: str,
                      creative_type: str, entry_meta: dict) -> dict:
    """Build a labeled corpus row; pulls simulated=1 and optional first-party result."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    base = {
        "evidence_key": f"corpus-{seed_suffix}-{headline[:12].lower().replace(' ', '_')}",
        "source_url": entry_meta.get("source_url", "https://corpus.example/campaign"),
        "creative_type": creative_type,
        "headline": headline,
        "body": body,
        "offer_text": offer,
        "cta_text": cta,
        "fetched_at": now,
        "simulated": True,
    }
    # Only include VERIFIED first-party numbers when explicitly supplied.
    if entry_meta.get("methodology") in ("first_party", "controlled_test"):
        base["methodology"] = entry_meta["methodology"]
        base["sample_size"] = entry_meta.get("sample_size")
        base["quantified_result"] = entry_meta.get("quantified_result")  # note: may be None
    elif "methodology" in entry_meta:
        base["methodology"] = entry_meta["methodology"]
    return base


def _corpus(region: str, seed_suffix: int) -> list[dict]:
    """Return a heterogeneous set: strong (A/B), a hypothesis (C), and hype / unsubstantiated (D)."""
    return [
        # ---- Strong, provable: quantified first-party demo campaigns -----------------
        _evidence_variant(seed_suffix,
            "Your competitor doesn't need better AI — they just answer the phone first",
            "Every missed customer call can be money walking out your door. We set up 24/7 "
            "AI answering that books appointments the moment they call. Urgent response.",
            "Free missed-call recovery demo for your business",
            "Book a free consultation", "ad_video",
            {"methodology": "first_party", "sample_size": 42, "quantified_result": True,
             "source_url": "https://corpus.example/aimbex_missed_call"}),
        _evidence_variant(seed_suffix,
            "How restoration businesses stop losing after-hours jobs",
            "Emergency water-damage customers often call several companies. The one that "
            "answers promptly gets the job. AI dispatch + booking keeps you first.",
            "Free 24/7 call-answering assessment", "Book free demo", "landing_page",
            {"methodology": "controlled_test", "sample_size": 35, "quantified_result": True,
             "source_url": "https://corpus.example/restoration_after_hours"}),

        # ---- Promising hypothesis: strong creative signals, no conversion numbers ----
        _evidence_variant(seed_suffix,
            "What most contractors don't realize about after-hours calls",
            "Did you know a large share of job inquiries come after 5pm? While answering "
            "services are common, an AI receptionist can book and qualify in one call.",
            "AI receptionist vs answering service — free walkthrough", "Get the walkthrough",
            "email", {"methodology": "hypothesis",
                      "source_url": "https://corpus.example/hypothesis_email"}),

        # ---- Hype / bad funnel: unsubstantiated, overpromising — should be DEMOTED ----
        _evidence_variant(seed_suffix,
            "Make $10,000/month guaranteed with AI — no risk, secret formula!",
            "Guaranteed ROI, unlimited income, 100% conversion. Get rich quick with this "
            "one weird trick nobody tells you. Clickbank secret.",
            "Instant traffic, never fail", "Buy now — limited time!", "ad_video",
            {"methodology": "unsubstantiated", "source_url": "https://corpus.example/hype_scam"}),
        _evidence_variant(seed_suffix,
            "HOT SALE! Unlimited leads overnight, guaranteed success",
            "Works overnight, no effort at all, guaranteed ROI. Trusted by nobody really "
            "but sounds amazing. Misleading offers everywhere.",
            "", "Sign up — act fast!", "funnel",
            {"methodology": "unsubstantiated", "source_url": "https://corpus.example/fake_funnel"}),
    ]


class CampaignCorpusAdapter(ProviderAdapter):
    name = "campaign_corpus"

    async def health_check(self) -> HealthResult:
        return HealthResult(True, self.name, "deterministic dev corpus (simulated data)")

    async def capabilities(self) -> list[str]:
        return ["research.pull", "research.sample"]

    async def execute(self, op: str, payload: dict) -> AdapterResult:
        if op not in ("research.pull", "research.sample"):
            return AdapterResult(False, {}, warnings=[f"campaign_corpus: unknown op '{op}'"])
        global _CORPUS_SEED
        _CORPUS_SEED += 1
        rows = _corpus(payload.get("region", "CA-AB"), _CORPUS_SEED)
        return AdapterResult(True, {"research": rows, "region": payload.get("region", "CA-AB"),
                                    "simulated": True})

    async def reconcile(self, external_id: str) -> ReconcileResult:
        return ReconcileResult(ok=True, external_id=external_id, found=False)

    async def handle_webhook(self, payload: dict) -> WebhookOutcome:
        return WebhookOutcome(processed=False)