# APEX COMMAND AI — build status (1 November 2026)
# Build 2 of the hour: real Google adapters + Workflow A + asset->publish gate.

Pilot tenant: AIMBEX Technologies  ·  Stack: FastAPI + asyncpg + Redis + PostgreSQL 16
CRM: **Odoo (no HighLevel)**  ·  Verification: `scripts/verify.py` — **41/41 checks green, exit 0**

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
- GA4/GSC/Ads/Meta/DataForSEO/HeyGen/Runway/ElevenLabs/Buffer/Metricool etc. are **adapter shells that fail closed** until credentials + endpoint wiring land — the routing slot exists, which is the point.
- Blueprint §8 preflight (VPS specs, Bluehost) still belongs to Raj; this box is the dev/pilot host.
- Demo seed rows are flagged (`provider_ref='demo:...'`).

## Next unblocked pieces (for the "keep going" session)

1. **Provision real Google creds** → set `GOOGLE_OAUTH_JSON` service account + `GSC_SITE_URL`/`GA4_PROPERTY_ID`, flip `provider_routes.demand.pull` to `gsc` (dev_demand is the verified fallback)
2. **Real Odoo 17** (docker-compose `--profile crm`), add `apex_external_ref` field on `crm.lead`, set `ODOO_MOCK=0`
3. **OpenRouter key** → content_briefs start producing real drafts (jobs already complete as `not_configured` when keyless)
4. P2: HeyGen two-avatar podcast (§5 workflow C)
5. Weekly Workflow A cron already scheduled (script `scripts/workflow_a_cron.sh`, Mon 06:00 local)

## Pitfalls recorded (this build)

- **Python-spawned `psql` is sandboxed on this box** — subprocess psql changes DON'T PERSIST. Use asyncpg for all DB ops (setup_db.py does; verify.py uses direct connections).
- API-key/tenant lookups happen *before* the tenant context exists → RLS hid them → migration 0007 adds SELECT-only auth-lookup policies (writes stay tenant-scoped).
- asyncpg returns jsonb columns as **strings** → re-load before hashing (approvals).
- redis-py `blpop` timeout races raise `redis.exceptions.TimeoutError` — catch it, don't catch builtin.
- Odoo mock ids must be unique per process, or the (tenant, crm_external_id) unique constraint collides across runs.
