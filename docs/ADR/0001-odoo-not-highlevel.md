# ADR-0001 — Odoo is the CRM; HighLevel is excluded

Status: Accepted (owner directive, 1 Nov 2026)
Owner: CEO / pilot tenant AIMBEX

## Context
Blueprint v1.0 named HighLevel as "first candidate" CRM. Owner mandate: **no HighLevel at
all**. Requirement: Odoo as the CRM, plus openness to other alternatives.

## Decision
- **Odoo (Community, 17)** is the canonical operational CRM: `crm.lead`, `res.partner`,
  `calendar.event`.
- APEX PostgreSQL remains the **system of record for events** (leads, consent, approvals,
  spend, margin). Odoo mirrors contacts/opportunities via a stable external ID
  (`apex_external_ref`).
- The CRM is reached ONLY through `app/integ/odoo_crm.py` behind the provider adapter
  interface. If Odoo ever needs replacing (HubSpot, Odoo→ERPNext, self-hosted SuiteCRM),
  a new adapter file + route row is enough — no pipeline code changes.

## Consequences
- No HighLevel SDK, keys, or terminology anywhere in the repo.
- CRM swap remains a config change, not a rewrite (ADR-0002).
- Odoo integration must be verified live during onboarding (XML-RPC/JSON-RPC entitlement,
  custom field creation on `crm.lead`).
