-- 0003_marketing.sql — campaigns, content, questions (§6)

CREATE TABLE campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL,
  channel text NOT NULL,               -- search|social|email|referral|phone
  objective text NOT NULL,             -- qualified_lead|awareness|booking
  budget_cap_minor bigint NOT NULL DEFAULT 0 CHECK (budget_cap_minor >= 0),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','paused','live','archived')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX campaigns_tenant_idx ON campaigns(tenant_id);

-- FK from 0002's lead_events now that campaigns exists
ALTER TABLE lead_events
  ADD CONSTRAINT lead_events_campaign_fk FOREIGN KEY (campaign_id) REFERENCES campaigns(id);

-- Reconciled daily media spend (from provider reports, never self-reported by agents)
CREATE TABLE campaign_spend_daily (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  campaign_id uuid NOT NULL REFERENCES campaigns(id),
  spend_date date NOT NULL,
  spend_minor bigint NOT NULL DEFAULT 0 CHECK (spend_minor >= 0),
  currency char(3) NOT NULL DEFAULT 'CAD',
  provider_ref text NOT NULL,          -- GA4/Ads report id
  UNIQUE (tenant_id, campaign_id, spend_date, provider_ref)
);
CREATE INDEX spend_daily_campaign_idx ON campaign_spend_daily(campaign_id);

-- Drafts, renders and lineage (§5 workflow C)
CREATE TABLE content_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  campaign_id uuid REFERENCES campaigns(id),
  kind text NOT NULL,                  -- article|landing|script|video|podcast|ad|email
  object_uri text,
  rights_json jsonb NOT NULL DEFAULT '{}',  -- likeness/voice/asset rights evidence
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','approved','rejected','published','failed')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assets_tenant_idx ON content_assets(tenant_id);

-- Posted asset receipts (post id / URL / UTM — §5 workflow A step)
CREATE TABLE publications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  asset_id uuid NOT NULL REFERENCES content_assets(id),
  platform text NOT NULL,
  external_id text,
  published_at timestamptz,
  url text,
  utm_json jsonb NOT NULL DEFAULT '{}'
);

-- Research for content planning (GSC/DataForSEO/CRM objections)
CREATE TABLE customer_questions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  normalized_question text NOT NULL,
  intent text NOT NULL CHECK (intent IN ('emergency','price','comparison','trust','consultation','informational')),
  locale text NOT NULL DEFAULT 'CA-AB',
  evidence_json jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX questions_tenant_intent_idx ON customer_questions(tenant_id, intent);
