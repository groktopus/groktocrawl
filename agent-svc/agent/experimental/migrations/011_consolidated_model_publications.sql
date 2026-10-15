-- Permit the consolidated publication table to retain non-fixture candidates.

ALTER TABLE research_staging.consolidated_publications
    DROP CONSTRAINT consolidated_publications_fixture_only_check;
ALTER TABLE research_staging.consolidated_publications
    ADD CONSTRAINT consolidated_publications_fixture_only_boolean
    CHECK (fixture_only IN (true, false));

ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version
    ADD CHECK (version IN (1,2,3,4,5,6,7,8,9,10,11));
UPDATE research_staging.schema_version SET version=11;
