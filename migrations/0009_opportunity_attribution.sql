-- 0009_opportunity_attribution.sql — reconcile closed-won -> original first-touch source (§10)
-- Computed by the 'attribution' worker handler; dashboard reads it for wins_by_source/campaign.
-- RLS enabled + policy so the app role is tenant-scoped here too.

CREATE TABLE opportunity_attribution (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  opportunity_id uuid NOT NULL REFERENCES opportunities(id),
  contact_id uuid NOT NULL REFERENCES contacts(id),
  source text NOT NULL,
  campaign_id uuid,
  utm_json jsonb NOT NULL DEFAULT '{}',
  confidence text NOT NULL DEFAULT 'first-touch',
  attributed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, opportunity_id)
);
CREATE INDEX opp_attr_tenant_source_idx ON opportunity_attribution(tenant_id, source);

ALTER TABLE opportunity_attribution ENABLE ROW LEVEL SECURITY;
CREATE POLICY opportunity_attribution_isolation ON opportunity_attribution
  USING (tenant_id = apex_tenant_id()) WITH CHECK (tenant_id = apex_tenant_id());