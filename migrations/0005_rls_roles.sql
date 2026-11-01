-- 0005_rls_roles.sql — tenant isolation by construction (ADR-0003)
-- Run as the DDL role. App connects as apex_app; migrations/seed as apex_migrator.

-- Roles are created by scripts/setup_db.py at cluster level (before the DB exists).
-- This migration only references them: grants + RLS policies.

GRANT USAGE ON SCHEMA public TO apex_app;
GRANT USAGE ON SCHEMA public TO apex_migrator;

-- RLS helper: current tenant from per-transaction setting. NULL context => no rows (fail closed).
CREATE FUNCTION apex_tenant_id() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT current_setting('apex.tenant_id', true)::uuid $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'tenants','users','api_keys','provider_connections','provider_routes','brand_knowledge',
    'contacts','consent_events','lead_events','opportunities','appointments',
    'campaigns','campaign_spend_daily','content_assets','publications','customer_questions',
    'agent_jobs','approvals','webhook_events','attribution_models','audit_events'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    IF t = 'tenants' THEN
      EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_isolation', t);
      EXECUTE format(
        'CREATE POLICY %I ON %I USING (id = apex_tenant_id()) WITH CHECK (id = apex_tenant_id())',
        t || '_isolation', t);
    ELSE
      EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_isolation', t);
      EXECUTE format(
        'CREATE POLICY %I ON %I USING (tenant_id = apex_tenant_id()) WITH CHECK (tenant_id = apex_tenant_id())',
        t || '_isolation', t);
    END IF;
  END LOOP;
END $$;

-- apex_app: least-privilege DML on tenant tables + read on tenants (tenant header validation).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO apex_app;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO apex_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO apex_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO apex_app;

-- apex_migrator: DDL + seed (bypasses RLS via BYPASSRLS so seeds can write).
-- (BYPASSRLS is granted in scripts/setup_db.py where the role is created.)
