-- 0016_conversion_dedupe.sql — make the Conversion Intelligence engine idempotent.
-- Build 14 re-inserted identical research/variants/playbooks on every run and mislabelled
-- playbook evidence levels. Derived rows are rebuilt; first-party experiment results are kept.

-- derived draft experiments + playbooks (no spend / no leads) were duplicated/mislabelled
DELETE FROM creative_experiments WHERE cost_minor = 0 AND qualified_leads = 0;
DELETE FROM conversion_playbooks;

ALTER TABLE campaign_research ADD COLUMN IF NOT EXISTS content_hash text;
UPDATE campaign_research
   SET content_hash = encode(sha256(convert_to(source_url || '|' || headline, 'UTF8')), 'hex')
 WHERE content_hash IS NULL;

-- keep the earliest row per (tenant, content_hash)
CREATE TEMP TABLE _keep_research AS
  SELECT DISTINCT ON (tenant_id, content_hash) id
    FROM campaign_research ORDER BY tenant_id, content_hash, collected_at, id;

UPDATE creative_experiments SET source_evidence_id = NULL
 WHERE source_evidence_id IN (
   SELECT e.id FROM conversion_evidence e
    WHERE e.research_id NOT IN (SELECT id FROM _keep_research));
DELETE FROM conversion_evidence WHERE research_id NOT IN (SELECT id FROM _keep_research);
DELETE FROM campaign_research   WHERE id NOT IN (SELECT id FROM _keep_research);
DROP TABLE _keep_research;

ALTER TABLE campaign_research ALTER COLUMN content_hash SET NOT NULL;
CREATE UNIQUE INDEX campaign_research_dedupe_idx ON campaign_research (tenant_id, content_hash);
CREATE UNIQUE INDEX conversion_playbooks_name_idx ON conversion_playbooks (tenant_id, name);
