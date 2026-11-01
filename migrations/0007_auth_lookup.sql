-- 0007_auth_lookup.sql — auth bootstrap over RLS (api_keys/tenants lookup before tenant known)
-- resolve_principal must find the key by hash BEFORE the tenant context exists, so the
-- app role gets SELECT-only lookup on these two reference tables (key hashes are opaque
-- verifiers, not secrets; tenant rows are names/status). All other tables stay fully
-- tenant-isolated; apex_app keeps NO write grant on these two.

REVOKE INSERT, UPDATE, DELETE ON api_keys, tenants FROM apex_app;

CREATE POLICY api_keys_auth_lookup ON api_keys FOR SELECT TO apex_app USING (true);
CREATE POLICY tenants_auth_lookup ON tenants FOR SELECT TO apex_app USING (true);
