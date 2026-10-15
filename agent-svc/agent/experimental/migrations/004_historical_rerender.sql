ALTER TABLE research_staging.publication_operations
    ADD COLUMN rerender_of uuid,
    ADD COLUMN research_digest text,
    ADD CHECK ((rerender_of IS NULL) = (research_digest IS NULL));
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2,3,4));
UPDATE research_staging.schema_version SET version=4;
