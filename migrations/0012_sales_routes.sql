-- 0012_sales_routes.sql — sales funnel tail (§9): proposal send + engagement acceptance
-- both complete as local receipts until a document/e-sign provider is credentialed
-- (routing is DATA, ADR-0002).

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'send:proposal', 'local', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'send:proposal');

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'accept:engagement', 'local', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'accept:engagement');