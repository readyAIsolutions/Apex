# APEX COMMAND AI — build status (1 November 2026)

Pilot tenant: AIMBEX Technologies  ·  Stack: FastAPI + asyncpg + Redis + PostgreSQL 16
CRM: **Odoo (no HighLevel)**  ·  Verification: `scripts/verify.py` — **31/31 checks green, exit 0**

## What is verified working (real execution, not asserted)

| §11 work package | Evidence (verify.py) |
|---|---|
| P0 Infrastructure / DB isolation | 7 migrations applied; RLS on 21 tenant tables; tenant A cannot read/update/insert tenant B rows (denied by the database) |
| P0 CRM + lead ingestion | Webhook → dedupe → contact → lead event → opportunity → **Odoo mirror** (mock) with external-ref reconcile; duplicate event_keys processed once |
| P0 Consent + approval gates | Opt-out blocks the pipeline (no lead event, no CRM write); approval create → approve → execute-once; tampered payload blocked (hash mismatch); expired approval rejected |
| P0 Analytics → CRM reporting | Dashboard returns reconciled spend ($305.90 seeded), CAC ($305.90/qualified lead), wins, revenue ($3,000), margin ($1,800) + explicit caveats — no fabricated attribution |
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

1. P1: wire `gsc` adapter (real GSC query pull) → seed `customer_questions` → run question-intelligence job
2. P1: `content_draft` handler → asset submit → approval → publish receipt (needs an OPENROUTER_API_KEY)
3. Real Odoo: provision Odoo 17 (docker-compose `--profile crm`), create `apex_external_ref` field on `crm.lead`, flip `ODOO_MOCK=0`
4. P2: HeyGen two-avatar podcast pipeline
5. Workflow A weekly cron (research → draft → QA → approve) — §5

## Pitfalls recorded (this build)

- **Python-spawned `psql` is sandboxed on this box** — subprocess psql changes DON'T PERSIST. Use asyncpg for all DB ops (setup_db.py does; verify.py uses direct connections).
- API-key/tenant lookups happen *before* the tenant context exists → RLS hid them → migration 0007 adds SELECT-only auth-lookup policies (writes stay tenant-scoped).
- asyncpg returns jsonb columns as **strings** → re-load before hashing (approvals).
- redis-py `blpop` timeout races raise `redis.exceptions.TimeoutError` — catch it, don't catch builtin.
- Odoo mock ids must be unique per process, or the (tenant, crm_external_id) unique constraint collides across runs.
