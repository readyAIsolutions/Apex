-- 0001_init.sql — base tables (§6)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  timezone text NOT NULL DEFAULT 'America/Edmonton',
  currency char(3) NOT NULL DEFAULT 'CAD',
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','trial')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  email text NOT NULL,
  role text NOT NULL DEFAULT 'analyst' CHECK (role IN ('analyst','editor','marketer','orchestrator','approver','executor','admin')),
  auth_subject text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);

-- API keys for the pilot (bearer tokens). Key itself never stored — sha256 hash only.
CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL,
  key_hash text NOT NULL UNIQUE,
  roles text[] NOT NULL DEFAULT '{analyst}',
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_keys_tenant_idx ON api_keys(tenant_id);

-- API access metadata; secrets live externally (env / secret manager), never in DB.
CREATE TABLE provider_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  provider text NOT NULL,
  secret_ref text NOT NULL,
  scopes text[] NOT NULL DEFAULT '{}',
  state text NOT NULL DEFAULT 'new' CHECK (state IN ('new','configured','disabled','error')),
  last_health_at timestamptz,
  last_error text
);

-- Data-driven routing: task_type -> provider/model/cost cap (ADR-0002)
CREATE TABLE provider_routes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  task_type text NOT NULL,
  provider text NOT NULL,
  model text,
  cost_cap_usd numeric(10,4) NOT NULL DEFAULT 0.10 CHECK (cost_cap_usd >= 0),
  enabled boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, task_type, provider)
);

-- Approved factual business memory (brand facts, price policy, service areas)
CREATE TABLE brand_knowledge (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  source text NOT NULL,
  fact text NOT NULL,
  verified_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
