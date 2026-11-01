-- 0006_seed_aimbex.sql — pilot tenant + approved brand memory + demo economics data
-- Deterministic tenant UUID so tests/ops can rely on it.
-- This file is the §9 pilot: offer, target areas, first campaigns.

SELECT set_config('apex.tenant_id', '11111111-1111-4111-8111-111111111111', false);

INSERT INTO tenants (id, name, timezone, currency, status)
VALUES ('11111111-1111-4111-8111-111111111111', 'AIMBEX Technologies', 'America/Edmonton', 'CAD', 'active')
ON CONFLICT (id) DO NOTHING;

-- Owner-confirmed brand memory (source versions tracked; to be replaced with legal/brand QA)
INSERT INTO brand_knowledge (tenant_id, source, fact, verified_at, expires_at) VALUES
('11111111-1111-4111-8111-111111111111', 'blueprint-v1.0-day1', 'AIMBEX Technologies sells AI automation for restoration firms and contractors.', now(), now() + interval '90 days'),
('11111111-1111-4111-8111-111111111111', 'blueprint-v1.0-day1', 'First market: restoration/contractor firms in Lloydminster and Edmonton, Alberta.', now(), now() + interval '90 days'),
('11111111-1111-4111-8111-111111111111', 'blueprint-v1.0-day1', 'Flagship offer: "Never Miss a Qualified Lead" AI consultation and demo.', now(), now() + interval '90 days'),
('11111111-1111-4111-8111-111111111111', 'blueprint-v1.0-day1', 'No guaranteed ROI claims may be made in any asset.', now(), now() + interval '90 days')
ON CONFLICT DO NOTHING;

-- Provider connections (secrets live in env per secret_ref; state=configured once health-check passes)
INSERT INTO provider_connections (tenant_id, provider, secret_ref, scopes, state) VALUES
('11111111-1111-4111-8111-111111111111', 'odoo', 'APEX_ODOO_*', '{crm.lead,res.partner,calendar.event}', 'configured'),
('11111111-1111-4111-8111-111111111111', 'openrouter', 'OPENROUTER_API_KEY', '{chat}', 'configured'),
('11111111-1111-4111-8111-111111111111', 'brevo', 'BREVO_API_KEY', '{email}', 'new'),
('11111111-1111-4111-8111-111111111111', 'dataforseo', 'DATAFORSEO_*', '{keywords,serp}', 'new'),
('11111111-1111-4111-8111-111111111111', 'ga4', 'GOOGLE_OAUTH_JSON', '{analytics}', 'new')
ON CONFLICT DO NOTHING;

-- Data-driven model routing (ADR-0002): edit rows, never code, to swap providers/models.
INSERT INTO provider_routes (tenant_id, task_type, provider, model, cost_cap_usd) VALUES
('11111111-1111-4111-8111-111111111111', 'draft.fast',       'openrouter', 'deepseek/deepseek-chat',             0.05),
('11111111-1111-4111-8111-111111111111', 'draft.strong',     'openrouter', 'anthropic/claude-sonnet-4',           0.60),
('11111111-1111-4111-8111-111111111111', 'verify.schema',    'openrouter', 'openai/gpt-4o-mini',                 0.05),
('11111111-1111-4111-8111-111111111111', 'crm.sync',         'odoo',       NULL,                                    0),
('11111111-1111-4111-8111-111111111111', 'crm.booking',      'odoo',       NULL,                                    0),
('11111111-1111-4111-8111-111111111111', 'email.send',       'brevo',      NULL,                                    0.00),
('11111111-1111-4111-8111-111111111111', 'research.keywords','dataforseo', NULL,                                    0.20)
ON CONFLICT DO NOTHING;

-- §9 pilot funnel seed (demo economics so the attribution dashboard has real rows)
INSERT INTO campaigns (id, tenant_id, name, channel, objective, budget_cap_minor, status) VALUES
('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', '11111111-1111-4111-8111-111111111111', 'Pilot: Missed-call recovery — Lloydminster', 'search', 'qualified_lead', 150000, 'live'),
('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', '11111111-1111-4111-8111-111111111111', 'Pilot: AI receptionist awareness — Edmonton', 'social', 'qualified_lead', 100000, 'draft')
ON CONFLICT DO NOTHING;

INSERT INTO campaign_spend_daily (tenant_id, campaign_id, spend_date, spend_minor, provider_ref) VALUES
('11111111-1111-4111-8111-111111111111', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', '2026-10-30', 12050, 'demo:gads:2026-10-30'),
('11111111-1111-4111-8111-111111111111', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', '2026-10-31', 9800, 'demo:gads:2026-10-31'),
('11111111-1111-4111-8111-111111111111', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', '2026-11-01', 8740, 'demo:gads:2026-11-01')
ON CONFLICT DO NOTHING;

-- One qualified demo deal (closed-won) so CAC/margin math is demonstrable, not fabricated
INSERT INTO contacts (id, tenant_id, full_name, email_hash, consent_status) VALUES
('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', '11111111-1111-4111-8111-111111111111',
 'Demo Restoration Co', encode(sha256('demo@restoration.example'::bytea), 'hex'), 'granted')
ON CONFLICT DO NOTHING;

INSERT INTO lead_events (tenant_id, contact_id, source, campaign_id, utm_json, event_key) VALUES
('11111111-1111-4111-8111-111111111111', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', 'ad',
 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
 '{"utm_source":"google","utm_medium":"cpc","utm_campaign":"missed-call-lloyd"}',
 'demo:lead:2026-10-30:0001')
ON CONFLICT DO NOTHING;

INSERT INTO opportunities (tenant_id, contact_id, crm_external_id, stage, projected_value, actual_value, margin, closed_at) VALUES
('11111111-1111-4111-8111-111111111111', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', 'DEMO-LEAD-0001', 'won', 3000, 3000, 1800, now() - interval '1 day')
ON CONFLICT (tenant_id, crm_external_id) DO NOTHING;

INSERT INTO attribution_models (tenant_id, name, version, rules_json) VALUES
('11111111-1111-4111-8111-111111111111', 'first-touch-utm', '1.0.0',
 '{"rule":"first touch wins by default","caveats":["unknown self-reported source","no phone-offline join yet"]}')
ON CONFLICT DO NOTHING;
