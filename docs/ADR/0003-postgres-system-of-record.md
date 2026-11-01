# ADR-0003 — PostgreSQL is system of record; RLS = tenant isolation; Redis = transport

Status: Accepted
Owner: CTO

## Context
The transaction and scheduling engine must stay independent of any chatbot session, and a
future multi-tenant SaaS must make cross-tenant access impossible by construction.

## Decision
1. **PostgreSQL (16) is the system of record** for every APEX entity (tenants, CRM mirror,
   campaigns, events, approvals, audit). All tables tenant-scoped, UUID PKs, UTC times.
2. **Row Level Security on every tenant-owned table.** App connects as `apex_app`
   (least privilege, no DDL). Migrations run as `apex_migrator`. Tenant context is set
   per transaction (`SET LOCAL apex.tenant_id = <uuid>`); policies use
   `current_setting('apex.tenant_id', true)::uuid` and fail CLOSED (unset context → no rows).
3. **Redis is transport only** (job queue, ephemeral state). No durable business state in
   Redis.
4. Destructive/approval flows are idempotent (unique keys, hash-checked), replay-safe,
   fully audited (`audit_events`).

## Consequences
- Cross-tenant denial is enforced by the database, not by app discipline (verified by test).
- Backup/restore of one Postgres covers all durable state (offsite encrypted, tested).
- Media stays OUT of Postgres (object storage later, per §8).
