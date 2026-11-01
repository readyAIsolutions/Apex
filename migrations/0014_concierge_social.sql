-- 0014_concierge_social.sql — Lead Concierge human-escalation flag + social route seed

ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS human_review boolean NOT NULL DEFAULT false;

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'publish:social', 'local', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'publish:social');