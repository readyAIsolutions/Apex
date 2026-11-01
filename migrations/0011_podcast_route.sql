-- 0011_podcast_route.sql — Workflow C render receipts stay local until a real
-- render provider is credentialed (HeyGen/Runway); routing is DATA (ADR-0002).

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'publish:podcast', 'local', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'publish:podcast');