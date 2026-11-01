"""Compliance / QA content agent (§3 §11) — deterministic pre-approval gate.

Flags banned outcome-promise / ROI language and unsupported statistics before an asset
is put before the human approver. It does NOT decide publication (human authority is the
gate); it surfaces pass/fail so the approver acts on evidence. No fabricated claims.
"""
import re

BANNED_ROI_RE = [
    re.compile(r"risk[\s-]*free", re.I),
    re.compile(r"no\s+risk", re.I),
    re.compile(r"100\s*%\s*guaranteed", re.I),
    re.compile(r"guaranteed[\s-]*(results|roi|income|sales|wins|leads|appointments|a\s+booking|conversions)", re.I),
    re.compile(r"money[\s-]*back\s+guarantee", re.I),
    re.compile(r"definitely\s+(will|make|close|capture)", re.I),
    re.compile(r"best[\s-]*in[\s-]*canada", re.I),
    re.compile(r"#1\s+in\s+canada", re.I),
    re.compile(r"only\s+way", re.I),
    # added in build 16 (found by the LLM-variant validator test): common promise phrasings
    re.compile(r"\b(we|i|aimbex)\s+(guarantee|promise|assure)\b", re.I),
    re.compile(r"\bguarantee[sd]?\b", re.I),
    re.compile(r"\b(zero|without\s+any|absolutely\s+no)\s+risk", re.I),
    re.compile(r"\b\d+\s*x\s+(roi|return|your\s+(money|investment|revenue|leads|sales))", re.I),
    re.compile(r"\b(double|triple|quadruple)\s+your\s+(revenue|sales|leads|income|profit)", re.I),
    re.compile(r"\bnever\s+(miss|lose)\s+(a|another|any)\s+(single\s+)?(call|lead|customer)\b", re.I),
]

# Numbers like "37% faster" or "$10k/month" need a citable source — flagged as warn.
UNSUPPORTED_STAT_RE = re.compile(r"\b\d{1,3}(\.\d+)?\s*%|\$\s?\d[\d,]*")

CASL_HINTS = ["opt-out", "unsubscribe", "consent"]


def qa_content(text: str) -> dict:
    low = text or ""
    findings = []

    for pat in BANNED_ROI_RE:
        m = pat.search(low)
        if m:
            findings.append({"level": "fail", "rule": "banned_outcome_promise",
                             "match": m.group(0)[:60]})

    stats = UNSUPPORTED_STAT_RE.findall(low)
    if stats:
        findings.append({"level": "warn", "rule": "unsupported_statistic",
                         "match": stats[:5]})

    has_consent = any(h in low.lower() for h in CASL_HINTS)
    findings.append({"level": "info", "rule": "consent_language",
                     "match": "present" if has_consent else "absent (add opt-out if nurturing)"})

    failures = [f for f in findings if f["level"] == "fail"]
    passed = not failures
    return {
        "pass": passed,
        "findings": findings,
        "recommended_next_actions":
            (["remove banned outcome-promise language"] if failures else [])
            + ["submit draft for human approval" if passed else "revise draft then re-QA"],
    }