-- Extend the shared import lifecycle without converting existing bundle bytes.
ALTER TABLE research_staging.import_operations
    ADD COLUMN bundle_schema text NOT NULL DEFAULT 'retained-artifact-bundle-prototype/1'
        CHECK (bundle_schema IN ('retained-artifact-bundle-prototype/1','retained-research-bundle-prototype/1')),
    ADD UNIQUE(scope_id,root_id,bundle_schema);
ALTER TABLE research_staging.imported_bundles
    ADD COLUMN bundle_schema text NOT NULL DEFAULT 'retained-artifact-bundle-prototype/1'
        CHECK (bundle_schema IN ('retained-artifact-bundle-prototype/1','retained-research-bundle-prototype/1')),
    ADD FOREIGN KEY(scope_id,root_id,bundle_schema)
        REFERENCES research_staging.import_operations(scope_id,root_id,bundle_schema);
ALTER TABLE research_staging.imported_bundles DROP CONSTRAINT imported_bundles_check;
ALTER TABLE research_staging.imported_bundles ADD CONSTRAINT imported_bundle_versioned_digest
    CHECK (digest=encode(sha256(convert_to(bundle_schema,'UTF8') || decode('00','hex') || payload),'hex'));
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2,3,4,5,6,7,8,9));
UPDATE research_staging.schema_version SET version=9;
