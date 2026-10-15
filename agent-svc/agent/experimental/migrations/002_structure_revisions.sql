-- Forward-only extension. Caller locks/checks version 1 in the same transaction.
CREATE TABLE research_staging.revision_operations (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    revision_id uuid NOT NULL DEFAULT gen_random_uuid(),
    generation bigint NOT NULL,
    parent_id uuid,
    reserved bigint NOT NULL CHECK (reserved > 0 AND reserved <= 1048576),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','committed','cancelled')),
    input_digest text,
    PRIMARY KEY (scope_id,root_id,revision_id),
    FOREIGN KEY (scope_id,root_id) REFERENCES research_staging.roots,
    CHECK ((state='committed') = (input_digest IS NOT NULL))
);
CREATE TABLE research_staging.revisions (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    parent_id uuid,
    payload bytea NOT NULL CHECK (octet_length(payload) <= 1048576),
    digest text NOT NULL,
    PRIMARY KEY (scope_id,root_id,revision_id),
    FOREIGN KEY (scope_id,root_id) REFERENCES research_staging.roots,
    FOREIGN KEY (scope_id,root_id,parent_id)
        REFERENCES research_staging.revisions DEFERRABLE INITIALLY DEFERRED,
    CHECK (digest = encode(sha256(convert_to('retained-structure-prototype/1','UTF8') || decode('00','hex') || payload),'hex'))
);
CREATE TABLE research_staging.revision_sources (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    PRIMARY KEY (scope_id,root_id,revision_id,snapshot_id),
    FOREIGN KEY (scope_id,root_id,revision_id) REFERENCES research_staging.revisions ON DELETE CASCADE,
    FOREIGN KEY (scope_id,root_id,snapshot_id) REFERENCES research_staging.snapshots
);
ALTER TABLE research_staging.roots ADD COLUMN current_revision uuid;
ALTER TABLE research_staging.roots ADD CONSTRAINT current_revision_ref
    FOREIGN KEY (scope_id,root_id,current_revision) REFERENCES research_staging.revisions DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2));
UPDATE research_staging.schema_version SET version=2;
