-- 0002_crm.sql — lead journey tables (§6 CRM mirror)
-- APEX owns event truth; Odoo mirrors contacts/opportunities via crm_external_id.

CREATE TABLE contacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  crm_external_id text,               -- Odoo res.partner / crm.lead id
  email_hash text,
  phone_hash text,
  full_name text,
  consent_status text NOT NULL DEFAULT 'unknown' CHECK (consent_status IN ('unknown','granted','opted_out')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX contacts_tenant_email_idx ON contacts(tenant_id, email_hash);
CREATE INDEX contacts_tenant_phone_idx ON contacts(tenant_id, phone_hash);
CREATE INDEX contacts_crm_external_idx ON contacts(crm_external_id);

-- Permission/suppression evidence (CASL-friendly)
CREATE TABLE consent_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  contact_id uuid NOT NULL REFERENCES contacts(id),
  channel text NOT NULL,               -- email|phone|sms|social
  basis text NOT NULL,                 -- explicit|implied|opt_out|suppression
  status text NOT NULL DEFAULT 'granted' CHECK (status IN ('granted','opted_out')),
  proof_ref text,                      -- form submission id / recording ref
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consent_events_contact_idx ON consent_events(contact_id);

-- First and subsequent marketing touchpoints (UTM preserved, never dropped)
CREATE TABLE lead_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  contact_id uuid NOT NULL REFERENCES contacts(id),
  source text NOT NULL,                -- webform|phone|email|ad|organic|referral
  campaign_id uuid,                   -- FK added in 0003 (campaigns created there)
  utm_json jsonb NOT NULL DEFAULT '{}',
  occurred_at timestamptz NOT NULL DEFAULT now(),
  event_key text NOT NULL,             -- external idempotency key
  UNIQUE (tenant_id, event_key)
);
CREATE INDEX lead_events_contact_idx ON lead_events(contact_id);
CREATE INDEX lead_events_campaign_idx ON lead_events(campaign_id);

CREATE TABLE opportunities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  contact_id uuid NOT NULL REFERENCES contacts(id),
  crm_external_id text,                -- Odoo crm.lead id
  stage text NOT NULL DEFAULT 'new' CHECK (stage IN ('new','qualified','proposal','won','lost')),
  projected_value numeric(12,2),
  actual_value numeric(12,2),
  margin numeric(12,2),
  closed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX opportunities_contact_idx ON opportunities(contact_id);
CREATE INDEX opportunities_crm_external_idx ON opportunities(crm_external_id);
-- one external CRM id per tenant (used by seed's ON CONFLICT; NULLs allowed multiple times)
ALTER TABLE opportunities
  ADD CONSTRAINT opportunities_tenant_crm_ext_uniq UNIQUE (tenant_id, crm_external_id);

CREATE TABLE appointments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  contact_id uuid NOT NULL REFERENCES contacts(id),
  opportunity_id uuid REFERENCES opportunities(id),
  starts_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','attended','no_show','cancelled')),
  calendar_external_id text,           -- Odoo calendar.event id
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX appointments_contact_idx ON appointments(contact_id);
