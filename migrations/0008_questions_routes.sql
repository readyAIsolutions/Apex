-- 0008_questions_routes.sql — dedupe customer questions + route seeds for Workflow A
-- Demand-source routing is DATA (ADR-0002): point 'demand.pull' at gsc/dataforseo
-- when credentialed pulls exist; 'publish:site' stays on the local executor until a
-- CMS adapter is provisioned.

CREATE UNIQUE INDEX customer_questions_tenant_q_idx
  ON customer_questions (tenant_id, normalized_question);

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'demand.pull', 'dev_demand', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'demand.pull');

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'publish:site', 'local', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'publish:site');
