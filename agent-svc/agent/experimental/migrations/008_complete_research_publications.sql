CREATE TABLE research_staging.research_publication_operations (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    publication_id uuid NOT NULL DEFAULT gen_random_uuid(),
    revision_id uuid NOT NULL,
    generation bigint NOT NULL,
    context_digest text NOT NULL,
    revision_digest text NOT NULL,
    reserved bigint NOT NULL CHECK (reserved > 0 AND reserved <= 1048576),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','committed','cancelled')),
    input_digest text,
    rerender_of uuid,
    PRIMARY KEY(scope_id,root_id,publication_id),
    FOREIGN KEY(scope_id,root_id) REFERENCES research_staging.roots,
    CHECK ((state='committed') = (input_digest IS NOT NULL))
);
CREATE TABLE research_staging.research_publications (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    publication_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    context_digest text NOT NULL,
    revision_digest text NOT NULL,
    payload bytea NOT NULL,
    digest text NOT NULL,
    summary bytea NOT NULL,
    analysis bytea NOT NULL,
    dossier bytea NOT NULL,
    PRIMARY KEY(scope_id,root_id,publication_id),
    FOREIGN KEY(scope_id,root_id,revision_id) REFERENCES research_staging.research_revisions,
    CHECK (octet_length(payload)+octet_length(summary)+octet_length(analysis)+octet_length(dossier) <= 1048576),
    CHECK (digest=encode(sha256(convert_to('retained-research-publication-prototype/1','UTF8') || decode('00','hex') || payload),'hex'))
);
CREATE TABLE research_staging.research_publication_sources (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    publication_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    PRIMARY KEY(scope_id,root_id,publication_id,snapshot_id),
    FOREIGN KEY(scope_id,root_id,publication_id) REFERENCES research_staging.research_publications ON DELETE CASCADE,
    FOREIGN KEY(scope_id,root_id,snapshot_id) REFERENCES research_staging.snapshots
);
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2,3,4,5,6,7,8));
UPDATE research_staging.schema_version SET version=8;
