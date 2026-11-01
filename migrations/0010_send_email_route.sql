-- 0010_send_email_route.sql — outbound email route seed (§5 Workflow E / §3 Email agent)
-- Routing is DATA (ADR-0002): swap 'send.email' to any adapter (brevo, odoo Mailing,
-- resend...) with a rows change. campaign_spend_daily already has its idempotency
-- UNIQUE (tenant_id, campaign_id, spend_date, provider_ref).

INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd)
SELECT '11111111-1111-4111-8111-111111111111', 'send:email', 'brevo', NULL, 0.00
WHERE NOT EXISTS (SELECT 1 FROM provider_routes
                  WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND task_type = 'send:email');