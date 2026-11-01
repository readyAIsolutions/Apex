-- 0017_conversion_llm_routes.sql — LLM routes for the Conversion Intelligence engine (data, ADR-0002).
-- research.analyze.llm: cheap model reads untrusted ad copy -> structured principle (validated, cached)
-- draft.strong (seeded in 0006) writes the original variants.
INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'research.analyze.llm', 'openrouter', 'deepseek/deepseek-chat', 0.05
WHERE EXISTS (SELECT 1 FROM tenants WHERE id = '11111111-1111-4111-8111-111111111111')
  AND NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'research.analyze.llm');
