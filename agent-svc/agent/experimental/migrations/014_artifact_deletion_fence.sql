-- Permit a research tombstone to exist before a late artifact publication.
ALTER TABLE research_staging.research_artifact_sets ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE research_staging.research_artifact_sets ALTER COLUMN artifact_set_id DROP NOT NULL;
ALTER TABLE research_staging.research_artifact_sets
    DROP CONSTRAINT research_artifact_sets_total_bytes_check;
ALTER TABLE research_staging.research_artifact_sets
    ADD CHECK (total_bytes BETWEEN 0 AND 33554432);
ALTER TABLE research_staging.research_artifact_sets
    DROP CONSTRAINT research_artifact_sets_check;
ALTER TABLE research_staging.research_artifact_sets
    ADD CHECK (
        (NOT deleted AND manifest IS NOT NULL AND run_id IS NOT NULL
            AND artifact_set_id IS NOT NULL AND total_bytes > 0)
        OR
        (deleted AND manifest IS NULL)
    );
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version
    ADD CHECK (version IN (1,2,3,4,5,6,7,8,9,10,11,12,13,14));
UPDATE research_staging.schema_version SET version=14;
