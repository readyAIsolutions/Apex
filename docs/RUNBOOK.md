# APEX COMMAND AI — Operations Runbook (v1, 1 Nov 2026)

Goal: run the AIMBEX pilot funnel (lead → booking → proposal → signed → revenue) with
humans in every gate that spends, sends, publishes, or commits.

## Stack (this dev box)
- API: FastAPI on uvicorn :8100, `/docs` OpenAPI (see generated OPENAPI.json)
- DB: native PostgreSQL 16.15 on 127.0.0.1:5432, database `apex`
  - `apex_migrator` = DDL/BYPASSRLS (migrations only)
  - `apex_app` = runtime (RNLS-scoped)
- Queue: Redis :6379/4 — transport only; PostgreSQL is the system of record
- Worker: `worker/run.py` BRPOPs jobs, never auto-approves human gates

## Start / stop
```bash
cd /home/nova/apex
.venv/bin/python scripts/setup_db.py                 # idempotent; applies pending migrations
.venv/bin/uvicorn app.main:create_app --factory --port 8100 &   # API
.venv/bin/python -m worker.run &                     # worker
# verify the whole platform:
.venv/bin/python scripts/verify.py                   # 108 checks; exit 0 = green
.venv/bin/python scripts/ops_check.py                # ops report; exit 1 on alerts
.venv/bin/python scripts/backup.py                  # logical snapshot -> backups/ (keep 7)
```

## Daily ops
1. `ops_check.py` — watch queue depth, unprocessed webhooks, stalled jobs, disk, backup age.
2. Approval queue: `GET /v1/approvals?state=pending` (approver key) — decide gates.
3. Dashboard: `GET /v1/dashboard/overview` — spend, CAC, wins, margin, wins_by_source/campaign, referral_links.
4. Weekly (Monday 06:00 local, cron): Workflow A demand research → question store → content briefs.

## Pilot guardrails (verified behaviors)
- Opt-out stops automation at ingest AND at send execution (CASL).
- QA blocks banned outcome-promise copy at submit (409) unless an editor forces.
- Budget increases refused (409) unless the optimizer permits scale.
- Engagements can't be won without a published proposal receipt.
- Podcast render requires documented likeness + voice permission.
- Paid link placements require an explicit disclosure flag.
- Second tenant (even admin) cannot read or touch tenant A data.

## Recovery
- Restore test: `app/services/backup.py::restore_snapshot` round-trips into a scratch DB
  (covered by verify section 10). Real-world: restore the latest `backups/apex_snapshot_*.jsonl.gz`
  into a fresh database (schema migrations 0001-0009 + human_review ALTER, then snapshot data).
- Provider outages: adapters fail CLOSED (result recorded, approval stays executable/re-issued).
- Ops WARNING (this box): root disk ~0.7% free — free space before growing backups/venv/data.

## Day-14 go/no-go checklist (blueprint §12)
- [ ] Real Google service-account creds → `provider_routes.demand.pull = gsc`
- [ ] Real Odoo 17 (docker-compose `--profile crm`) + apex_external_ref on crm.lead → `ODOO_MOCK=0`
- [ ] OPENROUTER_API_KEY (real drafts), BREVO_API_KEY + plaintext-recipient source (Odoo)
- [ ] HEYGEN_API_KEY / RUNWAY_API_KEY + avatar rights refs
- [ ] Confirm exact AIMBEX branding/domain; set budget ceilings; sign-off on prices
- [ ] Launch limited approved campaign; manual publishes; re-run verify + ops_check weekly

Swap-in model (ADR-0002): any software can be replaced by adding `app/integ/<name>.py`
and pointing `provider_routes` at it — routing is DATA, never code.
