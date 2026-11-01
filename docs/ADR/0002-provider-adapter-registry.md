# ADR-0002 — Provider adapter registry (software is replaceable by design)

Status: Accepted
Owner: CTO / Developer

## Context
Requirement: "build this in sections so that software can be added and replaced as new
better software comes out." Vendors change pricing, quality, and API tiers constantly;
the platform must not couple business logic to any vendor SDK.

## Decision
Every external service implements the same adapter contract
(`app/integ/base.py`):

```
health_check() -> HealthResult
capabilities() -> list[str]
get_usage_cost() -> CostReport
execute(op: str, payload: dict) -> AdapterResult
reconcile(external_id: str) -> ReconcileResult
handle_webhook(payload: dict) -> WebhookOutcome
```

- Adapters are registered in `app/integ/__init__.py` (REGISTRY dict).
- Routing is DATA-DRIVEN: `provider_routes(task_type, provider, model, cost_cap)` rows
  (fallback: env `APEX_ROUTE_<TASK>=<provider>`). The router health-checks before use,
  fails closed, and never hard-codes a vendor in business logic.
- Adding software = new adapter file + registration + route row. Replacing = point the
  route elsewhere and disable the old provider_connection. Deleting = remove the row.
- No access tokens in prompts or client-visible logs; secrets live in env / secret manager.

## Consequences
- Verticals (leads, content, media, ads, email) only import the interface.
- Tests use mock adapters (`APEX_*_MOCK=1`) so the whole slice runs with zero keys.
