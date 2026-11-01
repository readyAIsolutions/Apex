-- 0015_conversion_intel.sql — Conversion Intelligence & Creative Evolution Engine (§4/§14)
-- Filters out bad funnels/copy; grades evidence A-D; reverse-engineers winning funnels;
-- stores original creative experiments + reuse playbooks. Tenant-isolated (RLS) like 0009.

-- 1) CAMPAIGN RESEARCH — publicly observed campaigns/funnels/videos/copy + features + source.
CREATE TABLE campaign_research (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  provider text NOT NULL,                 -- meta_ads_library|google_ads_clt|semrush|manual|mock_corpus
  source_url text NOT NULL,
  creative_type text NOT NULL CHECK (creative_type IN
    ('ad_video','sales_video','landing_page','funnel','email','sms','blog','offer')),
  headline text NOT NULL DEFAULT '',
  hook_first_3s text,
  offer_text text,
  claims text,                            -- exact performance claims made by the campaign
  copy_features jsonb NOT NULL DEFAULT '[]',   -- [emotional_trigger, cta_style, objection_handled, ...]
  observed_signals jsonb NOT NULL DEFAULT '{}',-- public signal: views/share/engagement (may be absent)
  confidential_evidence bool NOT NULL DEFAULT false,  -- obtained only via authorized access
  collected_at timestamptz NOT NULL DEFAULT now(),
  collected_by text NOT NULL DEFAULT 'campaign_hunter'
);
CREATE INDEX campaign_research_tenant_idx ON campaign_research(tenant_id, creative_type);

-- 2) CONVERSION EVIDENCE — claimed vs verified metrics, methodology, confidence, grade A-D.
CREATE TABLE conversion_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  research_id uuid REFERENCES campaign_research(id),
  grade char(1) NOT NULL CHECK (grade IN ('A','B','C','D')),
  claimed_metrics jsonb NOT NULL DEFAULT '{}',   -- {conversion_rate, cpl, roas, ...} AS CLAIMED
  verified_metrics jsonb NOT NULL DEFAULT '{}',  -- only first-party / auditable values
  sample_size int,                              -- null = unknown
  methodology text NOT NULL CHECK (methodology IN
    ('first_party','controlled_test','case_study','hypothesis','unsubstantiated')),
  confidence text NOT NULL DEFAULT 'low',
  provenance text NOT NULL DEFAULT '',          -- where the grade comes from
  unscored_notes text,                          -- why it's a C/D (buzzword version of grade)
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, research_id)
);
CREATE INDEX conversion_evidence_tenant_grade_idx ON conversion_evidence(tenant_id, grade);

-- 3) CREATIVE EXPERIMENTS — variants, audience, budgets, test design, outcomes.
CREATE TABLE creative_experiments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  variant_name text NOT NULL,
  source_evidence_id uuid REFERENCES conversion_evidence(id),  -- principle this variant originated from
  audience text NOT NULL DEFAULT '',
  variant_json jsonb NOT NULL DEFAULT '{}',     -- {headline, hook, cta, funnel_position, ...}
  status text NOT NULL DEFAULT 'draft' CHECK (status IN
    ('draft','running','won','lost','paused','queued')),
  budget_cap_minor bigint NOT NULL DEFAULT 0 CHECK (budget_cap_minor >= 0),
  qualified_leads int NOT NULL DEFAULT 0,
  cost_minor bigint NOT NULL DEFAULT 0,
  closed_value_minor bigint NOT NULL DEFAULT 0,
  started_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX creative_experiments_tenant_idx ON creative_experiments(tenant_id, status);

-- 4) CONVERSION PLAYBOOKS — reusable, original strategies supported by accumulated evidence.
CREATE TABLE conversion_playbooks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL,
  industry text NOT NULL DEFAULT '',
  playbook_json jsonb NOT NULL DEFAULT '{}',      -- {principle, positioning, offer, cta, funnel}
  supporting_evidence_uuids jsonb NOT NULL DEFAULT '[]',
  evidence_level text NOT NULL DEFAULT 'hypothesis' CHECK (evidence_level IN
    ('verified','strong_support','hypothesis','unsubstantiated')),
  rights_json jsonb NOT NULL DEFAULT '{}',        -- originality / license / authorized-access notes
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversion_playbooks_tenant_idx ON conversion_playbooks(tenant_id, industry);

-- RLS for all four (same isolation policy model as 0009).
ALTER TABLE campaign_research       ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversion_evidence     ENABLE ROW LEVEL SECURITY;
ALTER TABLE creative_experiments    ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversion_playbooks    ENABLE ROW LEVEL SECURITY;
CREATE POLICY campaign_research_isolation      ON campaign_research   USING (tenant_id = apex_tenant_id()) WITH CHECK (tenant_id = apex_tenant_id());
CREATE POLICY conversion_evidence_isolation    ON conversion_evidence USING (tenant_id = apex_tenant_id()) WITH CHECK (tenant_id = apex_tenant_id());
CREATE POLICY creative_experiments_isolation   ON creative_experiments USING (tenant_id = apex_tenant_id()) WITH CHECK (tenant_id = apex_tenant_id());
CREATE POLICY conversion_playbooks_isolation   ON conversion_playbooks USING (tenant_id = apex_tenant_id()) WITH CHECK (tenant_id = apex_tenant_id());

-- Route seeds (data, ADR-0002): research.pull -> deterministic corpus until a real ads library
-- (meta_ads_library / semrush / similarweb) is credentialed; research.analyze stays local until
-- an OpenRouter reasoning model is provisioned. Guarded on tenant existence so this migration
-- is schema-safe to re-run in a fresh restore (no tenants yet -> inserts nothing; data from snapshot).
INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'research.pull', 'campaign_corpus', NULL, 0.00
WHERE EXISTS (SELECT 1 FROM tenants WHERE id = '11111111-1111-4111-8111-111111111111')
  AND NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'research.pull');

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'research.analyze', 'local', NULL, 0.00
WHERE EXISTS (SELECT 1 FROM tenants WHERE id = '11111111-1111-4111-8111-111111111111')
  AND NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'research.analyze');