CREATE INDEX live_scope_expiry ON research_staging.roots(scope_id,expires_at,root_id) WHERE NOT deleted;
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2,3,4,5,6));
UPDATE research_staging.schema_version SET version=6;
