# APEX COMMAND AI — build status (1 November 2026)
# Build 11: sales funnel tail — proposal -> signed engagement -> closed-won with economics.

Pilot tenant: AIMBEX Technologies  ·  Stack: FastAPI + asyncpg + Redis + PostgreSQL 16
CRM: **Odoo (no HighLevel)**  ·  Verification: `scripts/verify.py` — **96/96 checks green, exit 0**

## What is verified working (real execution, not asserted)

| §11 work package | Evidence (verify.py) |
|---|---|
| P0 Infrastructure / DB isolation | 7 migrations applied; RLS on 21 tenant tables; tenant A cannot read/update/insert tenant B rows (denied by the database) |
| P0 CRM + lead ingestion | Webhook → dedupe → contact → lead event → opportunity → **Odoo mirror** (mock) with external-ref reconcile; duplicate event_keys processed once |
| P0 Consent + approval gates | Opt-out blocks the pipeline (no lead event, no CRM write); approval create → approve → execute-once; tampered payload blocked (hash mismatch); expired approval rejected |
| P0 Analytics → CRM reporting | Dashboard returns reconciled spend ($305.90 seeded), CAC ($305.90/qualified lead), wins, revenue ($3,000), margin ($1,800) + explicit caveats — no fabricated attribution |
| **Build 2 — demand intelligence (Workflow A)** | `scripts/workflow_a.py` pulls demand via data-driven route (`dev_demand` simulated → swap to real gsc), stores evidence-backed questions (dedupe, evidence timestamps/source, **no fabricated volumes**), clusters by intent, queues content briefs, worker completes them — verified E2E (30 pulled, 30 stored, top-3 clusters, 3 brief jobs) |
| **Build 2 — full asset→approval→publish gate over HTTP** | `POST /v1/assets` (editor+) → submit → approve (approver key) → execute (executor key) → **publication receipt row + asset flipped published**, all in one transaction, verified |
| **Build 2 — real Google adapters** | `gsc` + `ga4` implement real service-account OAuth (RS256 JWT, `cryptography`) + real API calls; fail CLOSED without creds; request/auth shapes unit-tested with a fake transport |
| **Build 3 — SEO Auditor** | deterministic on-page scanner (`app/services/seo.py`, stdlib-only): title/description/h1/canonical/viewport/noindex/JSON-LD/alt/word-count → findings + tickets; sync `POST /v1/seo/audit` (editor+, RBAC-tested) AND async `seo_auditor` job — both verified |
| **Build 3 — attribution reconcile (§10)** | `attribution` worker joins closed-won opportunities to their original first-touch lead event (source/campaign) into `opportunity_attribution`; dashboard exposes `wins_by_source`/`wins_by_campaign`; verified: seeded won deal → source `ad` |
| **Build 4 — keyless content lifecycle** | `content_draft` falls back to a clearly-labelled SIMULATED template draft (no hard failure) → briefs ALWAYS produce draft `content_assets` → submit → approve → publish → receipt; verified keyless (11 articles) |
| **Build 4 — lead booking (§5 Workflow B)** | `POST /v1/leads/{contact_id}/appointments` (editor+) books an approved consultation slot via Odoo calendar mirror; verified (mock external id) |
| **Build 4 — approval queue** | `GET /v1/approvals?state=pending` (approver+) lists the operator's open gates; verified to surface the pending publish gate |
| **Build 5 — Paid Media Optimizer (recommend-only §11 P1)** | deterministic economics gate: refuses scale-ups on tiny samples / CAC past 25% ceiling / budget breach; holds until signed work. `GET /v1/ads/optimizer/recommend` (marketer+) + `paid_media` job — verified refuse on the demo campaign (1 qualified < 3) |
| **Build 5 — Compliance/QA content gate (§3 §11)** | deterministic checks: banned outcome-promise/ROI phrases (fail) + uncited statistics/consent (warn); `POST /v1/qa/content` (editor+) + `content_qa` job — verified flags banned copy, passes clean copy; template drafts reworded to pass clean |
| **Build 6 — QA as a publish-gate precondition** | `POST /v1/assets/{id}/submit` runs the compliance gate on submitted copy: banned copy → **409 qa_failed**; `force:true` override (human authority wins) → submits; clean copy → submits → approval. Verified all three paths |
| **Build 7 — optimizer-guarded budget changes (§10)** | `POST /v1/campaigns/{id}/budget` — increases are **refused (409)** unless the recommend-only optimizer would permit scale; reductions always pass. Verified: increase blocked on the tiny-sample demo, decrease allowed |
| **Build 7 — second-tenant isolation (P3 signal)** | a second tenant's **admin** key sees only its own (empty) dashboard and cannot touch tenant A's assets (404) — isolation held even with elevated roles |
| **Build 8 — backup/restore proof (§11 P0)** | `app/services/backup.py` logical snapshot (22 tables, gzip-JSONL + manifest) → restore into a scratch DB via schema migrations + typed inserts → verified round-trip: tenants/contacts/cross-tenant rows + RLS survive restore, scratch dropped. asyncpg-only (subprocess psql/pg_dump is sandboxed on this box) |
| **Build 8 — ops health/alert (§8)** | `scripts/ops_check.py`: queue depth, unprocessed webhooks, stalled jobs, DB size, disk free, backup freshness → report + exit 1 on alerts. Correctly detects the box's real disk condition (0.7% free = alert) |
| **Build 9 — Email/Retention (§3 §5 Workflow E)** | `email_nurture` agent: opt-out stops automation at CREATE (no approval/send); granted contact gets a `send:email` human-gated approval; the executor **re-checks consent at EXECUTION time** (409 on revocation between draft and send — CASL-safe); keyless send fails CLOSED (brevo: no key / no plaintext recipient) with the attempt recorded. All five paths verified |
| **Build 9 — spend intake (§10)** | `POST /v1/campaigns/{id}/spend` reconciles provider-reported spend idempotently (unique (campaign,date,provider_ref), ON CONFLICT upsert) → dashboard reflects it (405.90 after ingest) |
| **Build 10 — podcast pipeline (Workflow C §5 P2)** | `podcast_producer` agent: **likeness+voice permission hard stop** (no refs → blocked), script compliance QA (banned claims → blocked), deterministic storyboard + render manifest (full + 3 shorts, SIMULATED), human `publish:podcast` approval → local executor receipt → asset published. All six paths verified |
| **Build 11 — sales funnel tail (§9)** | `proposal_draft` (deterministic SIMULATED proposal, valued from the opportunity) → `send:proposal` approval → receipt (platform proposal); `close_engagement` **refuses to accept without a published proposal** (guard at creation AND at execution); `accept:engagement` marks the opportunity **won with actual_value + margin** + audit. Full journey now: webhook → booked → proposal → signed → won → dashboard economics. Verified end-to-end (worth 4500 / margin 2700) |
| Runtime | API boots (uvicorn), `/healthz` 200, `/ready` db up, `/docs` OpenAPI; worker pulls Redis jobs and completes them (job state succeeded) |

## Layout (blueprint § → code)

- §3 agent envelope & results → `app/schemas.py`, `worker/handlers.py`
- §4 connectivity registry → `app/integ/` (adapter interface + Odoo/OpenRouter/Brevo real; GA4/GSC/Ads/Meta/DataForSEO/HeyGen/Runway/ElevenLabs/etc. as fail-closed shells — `docs/ALTERNATIVES.md` shows every swap)
- §5 workflows → `worker/handlers.py`, `app/services/{leads,approvals,consent}.py`
- §6 schema + RLS → `migrations/0001–0007`
- §7 APIs → `app/routers/` (RBAC roles: analyst→editor→marketer→orchestrator→approver/executor→admin)
- §8 infra → `docker-compose.yml`, `Caddyfile.example`, `scripts/setup_db.py`
- §9 pilot seed → `migrations/0006_seed_aimbex.sql`
- §10 measurement → `app/routers/dashboard.py`
- §11 acceptance matrix → this file
- §13 open decisions → `docs/ALTERNATIVES.md`, ADRs in `docs/ADR/`

## Run it

```bash
cd /home/nova/apex
.venv/bin/python scripts/setup_db.py     # idempotent: roles, db, migrations, keys
.venv/bin/python scripts/verify.py        # 31 green checks
.venv/bin/uvicorn app.main:create_app --factory --port 8100   # API
.venv/bin/python -m worker.run            # queue worker (separate process)
```

API keys (dev defaults — CHANGE in `.env` before any external use):
`apex_dev_admin_key_change_me` (admin) · `apex_dev_analyst_key` · `apex_dev_executor_key` · `apex_dev_approver_key`

## Honest limits (nothing faked)

- Odoo adapter runs in **mock mode** (`ODOO_MOCK=1`) until a real Odoo instance is provisioned; the XML-RPC client code is real and CI-safe.
- GA4/GSC/Ads/Meta/DataForSEO/HeyGen/Runway/ElevenLabs/Buffer/Metricool etc. are **adapter shells that fail closed** until credentials + endpoint wiring land — the routing slot exists, which is the point (GSC/GA4 are now real but still need a service-account key).
- Blueprint §8 preflight (VPS specs, Bluehost) still belongs to Raj; this box is the dev/pilot host.
- Demo seed rows are flagged (`provider_ref='demo:...'`).

## Next unblocked pieces (for the "keep going" session)

1. **Provision real Google creds** → set `GOOGLE_OAUTH_JSON` service account + `GSC_SITE_URL`/`GA4_PROPERTY_ID`, flip `provider_routes.demand.pull` to `gsc` (dev_demand is the verified fallback)
2. **Real Odoo 17** (docker-compose `--profile crm`), add `apex_external_ref` field on `crm.lead`, set `ODOO_MOCK=0`
3. **OpenRouter key** → content_briefs start producing real drafts (jobs already complete as `not_configured` when keyless)
4. P2: HeyGen two-avatar podcast — pipeline is built & verified keyless (permission gate, storyboard, QA, approval, receipt); real renders need HEYGEN_API_KEY/RUNWAY_API_KEY + avatar rights refs. Provider adapters heygen.py/runway.py already exist (fails closed).
5. Real proposal delivery/e-sign: the funnel tail is verified keyless (local receipts); swap `send:proposal` route to a document/e-sign adapter when provisioned.
6. Full content lifecycle now runs keyless end-to-end (template drafts) — provisioning OPENROUTER_API_KEY swaps templates for real drafts with zero code change.
7. Recommend-only spend optimizer + QA are wired; optimizer also guards budget increases now. Remaining P1 financing: connect a real GA4/Ads spend feed (needs a service-account key) so `campaign_spend_daily` is reconciled from providers instead of demo rows.
8. **OPS WARNING — this box's root disk is ~0.7% free (3.3G/468G).** `ops_check.py` alerts on it. Do not grow backups/venv further; free disk before more builds (other projects on the box occupy the space).

## Pitfalls recorded (this build)

- **Python-spawned `psql` is sandboxed on this box** — subprocess psql changes DON'T PERSIST. Use asyncpg for all DB ops (setup_db.py does; verify.py uses direct connections).
- API-key/tenant lookups happen *before* the tenant context exists → RLS hid them → migration 0007 adds SELECT-only auth-lookup policies (writes stay tenant-scoped).
- asyncpg returns jsonb columns as **strings** → re-load before hashing (approvals).
- redis-py `blpop` timeout races raise `redis.exceptions.TimeoutError` — catch it, don't catch builtin.
- Odoo mock ids must be unique per process, or the (tenant, crm_external_id) unique constraint collides across runs.
