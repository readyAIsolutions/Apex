"""Unit tests (fast, no DB): hashes, clustering, mock Odoo adapter, router routing,
Google service-account OAuth, GSC request shape, dev-demand honesty."""
import asyncio
import json


from app.integ.google_oauth import build_assertion
from app.integ.odoo_crm import OdooCRMAdapter
from app.services.approvals import canonical_hash
from worker.handlers import cluster_by_intent, classify_intent


def test_canonical_hash_stable_across_key_order():
    a = canonical_hash({"b": 2, "a": 1, "nested": {"x": [1, 2]}})
    b = canonical_hash({"nested": {"x": [1, 2]}, "a": 1, "b": 2})
    assert a == b
    c = canonical_hash({"b": 2, "a": 1, "nested": {"x": [1, 3]}})
    assert a != c


def test_classify_intent():
    assert classify_intent("emergency flood cleanup now") == "emergency"
    assert classify_intent("how much does it cost") == "price"
    assert classify_intent("are you licensed and insured") == "trust"
    assert classify_intent("can you book a walkthrough") == "consultation"
    assert classify_intent("what is mold remediation") == "informational"


def test_cluster_dedupes():
    clusters = cluster_by_intent(["FLOOD damage emergency", "flood damage emergency",
                                  "cost of AI receptionist"])
    by_intent = {c["intent"]: c for c in clusters}
    assert by_intent["emergency"]["count"] == 1
    assert by_intent["price"]["count"] == 1


def test_mock_odoo_lifecycle():
    adapter = OdooCRMAdapter(mock=True)
    assert asyncio.run(adapter.health_check()).ok

    r = asyncio.run(adapter.execute("create_lead", {"full_name": "Acme", "external_ref": "ref-1"}))
    assert r.ok and r.data["id"]
    rec = asyncio.run(adapter.reconcile("ref-1"))
    assert rec.found and rec.external_id == str(r.data["id"])

    u = asyncio.run(adapter.execute("update_stage", {"id": r.data["id"], "stage": "qualified"}))
    assert u.ok and u.data["stage"] == "qualified"


def test_mock_odoo_unknown_op_fails_closed():
    adapter = OdooCRMAdapter(mock=True)
    r = asyncio.run(adapter.execute("nonsense", {}))
    assert r.ok is False and any("unknown op" in w for w in r.warnings)


def test_router_env_override(monkeypatch):
    from app.integ import Router
    from app.integ.base import ShellAdapter
    reg = {"ga4": ShellAdapter("ga4", []), "gsc": ShellAdapter("gsc", [])}
    router = Router(reg)
    monkeypatch.setenv("APEX_ROUTE_ANALYTICS", "ga4")
    adapter = asyncio.run(router.route("analytics"))
    assert adapter.name == "ga4"


# ----------------------------------------------------------------------------
# Google service-account OAuth + GSC request shape (no network: fake transport)
# ----------------------------------------------------------------------------
def _make_service_account():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return {"client_email": "apex-sa@test.iam.gserviceaccount.com",
            "private_key": pem}, key


def test_build_assertion_rs256():
    sa, pubkey = _make_service_account()
    token = build_assertion(sa, ["https://www.googleapis.com/auth/webmasters.readonly"], now=1_700_000_000)
    header_b64, claims_b64, sig_b64 = token.split(".")
    header, claims = json.loads(_b64url_decode(header_b64)), json.loads(_b64url_decode(claims_b64))
    assert header["alg"] == "RS256"
    assert claims["iss"] == sa["client_email"]
    assert claims["aud"] == "https://oauth2.googleapis.com/token"
    assert claims["exp"] == claims["iat"] + 3600
    # verify signature with the public key
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives import hashes
    pubkey.public_key().verify(
        _b64url_decode(sig_b64),
        f"{header_b64}.{claims_b64}".encode(),
        asym_padding.PKCS1v15(), hashes.SHA256())


def _b64url_decode(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_gsc_request_shape_with_fake_transport(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_JSON", json.dumps(_make_service_account()[0]))
    monkeypatch.setenv("GSC_SITE_URL", "sc-domain:aimbex.example")
    calls = []

    async def fake_transport(url, headers, body):
        calls.append((url, headers, body))
        if "oauth2.googleapis.com" in url:
            return {"access_token": "tok-123", "expires_in": 3600}
        return {"rows": [{"keys": ["water damage cost"], "clicks": 42, "impressions": 900,
                          "ctr": 0.0466, "position": 4.2}]}

    from app.integ.gsc import GSCAdapter
    from app.integ.google_oauth import GoogleOAuth
    adapter = GSCAdapter(oauth=GoogleOAuth(transport=fake_transport))
    res = asyncio.run(adapter.execute("demand.queries", {"start_date": "7daysAgo"}))
    assert res.ok and len(res.data["demand"]) == 1
    token_call, api_call = calls
    assert token_call[0] == "https://oauth2.googleapis.com/token"
    assert api_call[1]["Authorization"] == "Bearer tok-123"
    assert api_call[2]["dimensions"] == ["query"]
    assert res.data["demand"][0]["query"] == "water damage cost"
    assert res.data["demand"][0]["simulated"] is False


def test_gsc_fails_closed_without_creds(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_JSON", raising=False)
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    from app.integ.gsc import GSCAdapter
    adapter = GSCAdapter()
    assert asyncio.run(adapter.health_check()).ok is False
    res = asyncio.run(adapter.execute("demand.queries", {}))
    assert res.ok is False and any("not configured" in w for w in res.warnings)


def test_dev_demand_deterministic_and_honest():
    from app.integ.dev_demand import DevDemandAdapter
    adapter = DevDemandAdapter()
    a = asyncio.run(adapter.execute("demand.queries", {"locale": "CA-AB"})).data["demand"]
    b = asyncio.run(adapter.execute("demand.queries", {"locale": "CA-AB"})).data["demand"]
    assert len(a) == len(b) >= 25
    assert all(r["simulated"] is True for r in a)
    assert all("clicks" not in r and "impressions" not in r for r in a)  # no fabricated volumes
    keys_a = [r["evidence_key"] for r in a]
    keys_b = [r["evidence_key"] for r in b]
    assert keys_a != keys_b  # fresh evidence per run, deterministic shape


# ----------------------------------------------------------------------------
# Deterministic on-page SEO audit
# ----------------------------------------------------------------------------
GOOD_HTML = (
    "<!doctype html><html><head>"
    "<title>AI Receptionist for Restoration Firms in Edmonton</title>"
    "<meta name='description' content='AI receptionist, missed-call recovery and CRM follow-up "
    "built for restoration and contracting firms in Edmonton and Lloydminster.'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<link rel='canonical' href='https://aimbex.example/ai-receptionist'>"
    '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Service"}</script>'
    "</head><body><h1>Never Miss a Qualified Lead</h1>"
    "<img src='/call.jpg' alt='AI answering a call'>"
    + ("<p>word</p>" * 130) +
    "</body></html>"
)


def test_audit_seo_good_page():
    from app.services.seo import audit_seo
    res = audit_seo(GOOD_HTML, "https://aimbex.example/ai-receptionist")
    assert res["ok_all"] is True, res["checks"]
    assert res["title"].startswith("AI Receptionist")


def test_audit_seo_flags_problems():
    from app.services.seo import audit_seo
    bad = ("<html><body><img src='/x.png'><p>tiny</p></body></html>")
    res = audit_seo(bad, url="https://aimbex.example/broken")
    checks = {c["field"]: c["ok"] for c in res["checks"]}
    assert checks["title"] is False
    assert checks["h1"] is False            # no h1
    assert checks["structured_data.jsonld"] is False
    assert checks["img.alt"] is False       # missing alt
    assert checks["noindex"] is True        # default is fine (no noindex present)
    assert any("fix" in a for a in res["recommended_next_actions"])


# ----------------------------------------------------------------------------
# Paid Media Optimizer (recommend-only) + Compliance QA
# ----------------------------------------------------------------------------
def test_paid_media_refuses_tiny_sample():
    from app.services.paymedia import evaluate_campaign
    r = evaluate_campaign(spend_usd=100, qualified_leads=1, wins=0,
                          first_sale_usd=3000, budget_cap_usd=1500)
    assert r["status"] == "refuse_scale"
    assert any("sample too small" in x for x in r["reasons"])


def test_paid_media_refuses_high_cac():
    from app.services.paymedia import evaluate_campaign
    r = evaluate_campaign(spend_usd=8000, qualified_leads=10, wins=3,
                          first_sale_usd=3000, budget_cap_usd=15000)
    assert r["status"] == "refuse_scale"          # CAC 800 > 25% of 3000
    assert any("economically safe" in x for x in r["reasons"])


def test_paid_media_holds_without_wins():
    from app.services.paymedia import evaluate_campaign
    r = evaluate_campaign(spend_usd=200, qualified_leads=5, wins=0,
                          first_sale_usd=3000, budget_cap_usd=1500)
    assert r["status"] == "hold"


def test_paid_media_consider_test_within_ceiling():
    from app.services.paymedia import evaluate_campaign
    r = evaluate_campaign(spend_usd=500, qualified_leads=10, wins=3,
                          first_sale_usd=3000, budget_cap_usd=1500)
    assert r["status"] == "consider_test" and r["cac"] == 50.0
    assert "capped" in r["reasons"][0].lower()


def test_qa_flags_banned_roi_language():
    from app.services.content_qa import qa_content
    r = qa_content("guaranteed results with a risk-free setup, 200% more calls")
    assert r["pass"] is False
    assert any(f["level"] == "fail" for f in r["findings"])


def test_qa_passes_clean_copy():
    from app.services.content_qa import qa_content
    r = qa_content("Answers missed calls and books consultations. Opt-out anytime.")
    assert r["pass"] is True


def test_podcast_storyboard_deterministic():
    from worker.handlers import _podcast_storyboard
    a = _podcast_storyboard("x", "cta1")
    b = _podcast_storyboard("x", "cta1")
    assert a == b
    assert a["render_manifest"]["shorts"] == 3
    assert a["simulated"] is True


def test_podcast_permission_hard_stop():
    from worker.handlers import _podcast_permission_ok
    assert _podcast_permission_ok(None)[0] is False
    assert _podcast_permission_ok({"likeness": "ref:l"})[0] is False
    assert _podcast_permission_ok({"likeness": "ref:l", "voice": "ref:v"})[0] is True


def test_pr_score_honest():
    from worker.handlers import _pr_score
    assert _pr_score({"publisher": "lloydminster journal",
                      "relevance_terms": ["restoration", "contractor"]}, "restoration", "AB") >= 1
    assert _pr_score({"publisher": "unrelated blog"}, "restoration", "AB") == 0


def test_urgent_transfer_matcher():
    from app.services.consent import is_urgent
    assert is_urgent("we have an EMERGENCY flood in the basement") is True
    assert is_urgent("just checking pricing for next quarter") is False


def test_social_post_deterministic():
    from worker.handlers import _social_post
    a = _social_post("T", "linkedin")
    b = _social_post("T", "linkedin")
    assert a == b
    assert a["utm"]["utm_source"] == "linkedin"
    assert "utm_source=linkedin" in a["text"]


# ---- Conversion Intelligence & Creative Evolution Engine (§4/§14) ----


def test_evidence_grading_demotes_hype():
    from app.services.conversion_intel import grade_evidence, scan_for_hype, evidence_scanner_rules
    # provable first-party result -> A
    g, _ = grade_evidence(42, "first_party", True)
    assert g == "A"
    # unsubstantiated -> D no matter the claimed numbers
    g, _ = grade_evidence(None, "unsubstantiated", False)
    assert g == "D"
    # hypothesis with no quantified result -> C
    g, _ = grade_evidence(None, "hypothesis", False)
    assert g == "C"
    # hype lexicon actually flagged
    assert scan_for_hype("make $10,000 guaranteed, secret formula, get rich quick") != []
    assert scan_for_hype("we answer missed calls and book consultations") == []
    assert evidence_scanner_rules()


def test_copy_analysis_detects_angles():
    from app.services.conversion_intel import analyze_copy
    r = analyze_copy("Every missed call could be money walking out your door",
                     "missed calls cost you jobs", "Book a free consultation now")
    assert "problem_agitation" in r["headline_hook"]
    assert "booking" in r["cta_style"]
    assert "fear_missed_lead" in r["emotional_triggers"]
    assert "urgency" in r["emotional_triggers"]


def test_reverse_funnel_maps_journey():
    from app.services.conversion_intel import reverse_funnel
    fn = reverse_funnel("ad_video", "hook line", "free demo offer", "Book a consultation", "lp")
    assert fn["journey"][0]["step"] == "attract"
    assert fn["journey"][-1]["element"].startswith("cta:booking")
    assert fn["primary_action"].startswith("cta:booking")


def test_creative_evolution_is_original_and_hypothesis():
    from app.services.conversion_intel import evolve_variant
    brand = {"industry": "restoration contractors", "service": "24/7 AI receptionist",
             "area": "Edmonton"}
    v = evolve_variant({}, brand, {"creative_type": "ad_video", "headline": "every missed call",
                                   "offer_text": ""})
    assert v["hypothesis"] is True
    assert "restoration" in v["headline"]
    assert "Edmonton" in v["cta"]
    # must NOT copy the source headline verbatim
    assert "every missed call" not in v["headline"].lower()


def test_ab_test_refuses_eager_winner():
    from app.services.conversion_intel import ab_test_design
    d = ab_test_design({"variant_name": "control"}, [{"variant_name": "v1"}, {"variant_name": "v2"}])
    assert d["min_sample_for_winner"] >= 30
    assert "no 'winner' declared" in d["guardrail"]


def test_build_playbook_levels():
    from app.services.conversion_intel import build_playbook
    pb = build_playbook({"headline": "H", "cta": "C", "positioning_note": "principle"}, "A", "restoration")
    assert pb["evidence_level"] == "verified"
    pb2 = build_playbook({"headline": "H", "cta": "C", "positioning_note": "p"}, "D", "restoration")
    assert pb2["evidence_level"] == "unsubstantiated"


def test_campaign_corpus_honest():
    from app.integ.campaign_corpus import CampaignCorpusAdapter
    import asyncio
    res = asyncio.run(CampaignCorpusAdapter().execute("research.pull", {"region": "CA-AB"}))
    rows = res.data["research"]
    assert res.data["simulated"] is True
    # unsubstantiated rows must NOT carry fabricated quantified results
    for r in rows:
        if r.get("methodology") == "unsubstantiated":
            assert r.get("quantified_result") is None
    # corpus contains both strong and hype examples so the filter can be demonstrated
    assert any(r.get("methodology") == "first_party" for r in rows)
    assert any(r.get("methodology") == "unsubstantiated" for r in rows)
