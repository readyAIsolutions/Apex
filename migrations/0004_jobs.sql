-- 0004_jobs.sql — execution ledger: agents, approvals, webhooks, audit (§6)

CREATE TABLE agent_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  agent text NOT NULL,                 -- seo_research|content_writer|lead_concierge|...
  schema_version int NOT NULL DEFAULT 1,
  input_json jsonb NOT NULL DEFAULT '{}',
  output_json jsonb,
  state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','succeeded','failed','cancelled')),
  cost_usd numeric(10,4) NOT NULL DEFAULT 0,
  error text,
  idempotency_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX agent_jobs_tenant_state_idx ON agent_jobs(tenant_id, state);

-- Human gate with immutable action hash (§7: approval invalid if payload changes/expires)
CREATE TABLE approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  action_type text NOT NULL,           -- publish|send_campaign|spend|outreach|execute_crm
  payload jsonb NOT NULL,
  payload_hash text NOT NULL,          -- sha256 of canonical JSON
  decision text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','approved','rejected','expired','executed')),
  approver_id uuid,
  expires_at timestamptz NOT NULL,
  decided_at timestamptz,
  executed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX approvals_tenant_state_idx ON approvals(tenant_id, decision);

-- Idempotent, replay-safe webhook intake (UNIQUE = dedupe)
CREATE TABLE webhook_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  provider text NOT NULL,
  event_key text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}',
  payload_ref text,
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  UNIQUE (tenant_id, provider, event_key)
);
CREATE INDEX webhook_events_processed_idx ON webhook_events(processed_at);

-- Attribution calculation provenance (never claim perfect attribution)
CREATE TABLE attribution_models (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL,
  version text NOT NULL,
  rules_json jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Security and operational trace
CREATE TABLE audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  actor text NOT NULL,                 -- api_key:<name> | user:<email> | system | provider:<name>
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id uuid,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_tenant_created_idx ON audit_events(tenant_id, created_at);
