# APEX COMMAND AI — AIMBEX Technical Blueprint v1.0 (Build)

Owner / pilot tenant: AIMBEX Technologies
Status: **V1 foundation wired and verified** — modular sections, no HighLevel, Odoo CRM adapter live-ready.

Mission: acquire qualified B2B AI-automation opportunities, convert to paid engagements,
demonstrate repeatable unit economics BEFORE multi-tenant SaaS.

## Why this build is "software you can add and replace"

Every external tool sits behind a **provider adapter** (`app/integ/`). The app only ever
talks to the adapter interface — never to a vendor SDK. Routing is data-driven:

```
task_type -> provider_routes (DB or env) -> adapter (health-checked) -> execute()
```

- New/better software comes out → add one adapter file, register it, point the route row
  at it. Old software is disabled by flipping a route, no code rewrite.
- No HighLevel anywhere. Odoo is the canonical CRM (see ADR-0001).
- Every section of the blueprint maps to a directory (see "Layout" below).

## Layout (blueprint section -> code)

| Blueprint § | Where |
|---|---|
| §3 Agent contracts / envelope | `app/schemas.py`, `worker/handlers.py` |
| §4 API connectivity registry | `app/integ/` + `provider_connections` / `provider_routes` tables |
| §5 Event-driven workflows | `worker/handlers.py` + `app/services/leads.py`, `approvals.py` |
| §6 Relational schema + RLS | `migrations/` (0001–0015), `migrations/0005_rls_roles.sql` |
| §7 Internal APIs | `app/routers/` (OpenAPI at `/docs`) |
| §8 Infrastructure | `docker-compose.yml`, `Caddyfile.example`, `scripts/setup_db.py` |
| §9 Pilot campaigns | `migrations/0006_seed_aimbex.sql` (brand facts, offer, areas) |
| §10 Measurement guardrails | `app/routers/dashboard.py`, `app/services/dashboard.py` |
| §11 Work packages | `docs/STATUS.md` acceptance matrix |
| §13 Open decisions | `docs/ALTERNATIVES.md` (Odoo + alternatives swap map) |
| §14 Reference links | `docs/ALTERNATIVES.md` |

## Quickstart (this box: native Postgres :5432 + Redis :6379, no Docker needed)

```bash
cd /home/nova/apex
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                 # edit secrets; APEX_ODOO_MOCK=1 for offline CRM
.venv/bin/python scripts/setup_db.py # create roles+db, apply migrations, seed AIMBEX
.venv/bin/python scripts/verify.py    # RLS denial, approval gate, webhook idempotency, Odoo flow
.venv/bin/uvicorn app.main:create_app --factory --port 8100   # API
.venv/bin/python -m worker.run                                 # queue worker
# worker must run AFTER verify (verify pauses it) or alongside — both share Redis/Postgres
```

## The approval gate (non-bypassable, §11 P0)

1. Agent produces draft → `POST /v1/assets/{id}/submit` freezes payload → creates approval (immutable `payload_hash`).
2. Designated approver → `POST /v1/approvals/{id}/decision` (`approve`/`reject`). Approval invalid if payload changed or expired.
3. Dedicated executor → `POST /v1/actions/{id}/execute` runs the adapter op ONLY if `approved + hash matches + not expired + not already executed`. Every decision and execution is written to `audit_events`.
4. Opt-outs: `consent_events` ledger — an `opted_out` record stops the lead pipeline at the door.

## Odoo CRM (replacing HighLevel)

`app/integ/odoo_crm.py` — real XML-RPC client (`/xmlrpc/2/common`, `/xmlrpc/2/object`):
`health_check` (authenticate), `create_lead`, `create_contact`, `update_stage`,
`reconcile(external_id)`, `handle_webhook`. Odoo models: `crm.lead`, `res.partner`,
`calendar.event`. APEX is system of record for events; Odoo mirrors contacts/opportunities
via stable external IDs (`apex_external_ref`). With `APEX_ODOO_MOCK=1` the adapter is an
in-memory fake — verified by tests without a server. Point it at a real Odoo instance by
setting URL/db/user/password and removing mock. (Odoo 17 container included commented in
docker-compose for local testing.)

## Verification (what "done" means here)

`scripts/verify.py` proves, with real execution against the real DB:
- all migrations applied, roles exist, RLS enabled on every tenant table
- cross-tenant read/update denied (tenant A cannot see or alter tenant B rows)
- approval: create → approve → execute OK; tampered payload rejected; expiry respected
- consent: opt-out blocks pipeline processing
- webhook idempotency: same event_key processed once
- Odoo adapter (mock): create lead → reconcile by external id → stage update
- API smoke: auth required, dashboard overview returns real aggregates

## Security posture

- Row Level Security on every tenant-owned table; app connects as `apex_app`
  (least privilege), migrations as `apex_migrator`; tenant context set per-transaction
  (`SET LOCAL apex.tenant_id`).
- Secrets only in `.env` / external secret manager — never in git, never in prompts.
- Webhooks HMAC-signed (`X-APEX-Signature`), idempotent by `(tenant, provider, event_key)`.
- Publishing / sending / spending requires an executed approval — no autonomous outbound.

## Status

See `docs/STATUS.md` for the work-package acceptance matrix and what's next
(P0 items verified; P1 question engine + content drafts; P2 podcast; P3 billing).
