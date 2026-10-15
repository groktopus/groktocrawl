-- Extend the trusted-server import lifecycle for consolidated publications.
ALTER TABLE research_staging.import_operations
    DROP CONSTRAINT import_operations_bundle_schema_check;
ALTER TABLE research_staging.import_operations
    ADD CONSTRAINT import_operations_bundle_schema_check
    CHECK (bundle_schema IN (
        'retained-artifact-bundle-prototype/1',
        'retained-research-bundle-prototype/1',
        'retained-consolidated-bundle-prototype/1'
    ));
ALTER TABLE research_staging.imported_bundles
    DROP CONSTRAINT imported_bundles_bundle_schema_check;
ALTER TABLE research_staging.imported_bundles
    ADD CONSTRAINT imported_bundles_bundle_schema_check
    CHECK (bundle_schema IN (
        'retained-artifact-bundle-prototype/1',
        'retained-research-bundle-prototype/1',
        'retained-consolidated-bundle-prototype/1'
    ));
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version
    ADD CHECK (version IN (1,2,3,4,5,6,7,8,9,10,11,12));
UPDATE research_staging.schema_version SET version=12;
