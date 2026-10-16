ALTER TABLE research_staging.roots ADD COLUMN kind text NOT NULL DEFAULT 'native' CHECK (kind IN ('native','import'));
CREATE TABLE research_staging.import_operations (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    origin_scope_id uuid NOT NULL,
    origin_root_id uuid NOT NULL,
    publication_id uuid NOT NULL,
    origin_generation bigint NOT NULL CHECK (origin_generation > 0),
    bundle_digest text NOT NULL,
    context_digest text NOT NULL,
    reserved bigint NOT NULL CHECK (reserved > 0 AND reserved <= 1048576),
    grant_expires_at timestamptz NOT NULL,
    retained_until timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','committed','cancelled')),
    receipt_digest text,
    PRIMARY KEY(scope_id,root_id),
    FOREIGN KEY(scope_id,root_id) REFERENCES research_staging.roots,
    FOREIGN KEY(origin_scope_id,origin_root_id) REFERENCES research_staging.roots(scope_id,root_id),
    CHECK ((state='committed') = (receipt_digest IS NOT NULL))
);
CREATE INDEX import_origin ON research_staging.import_operations(origin_scope_id,origin_root_id);
CREATE TABLE research_staging.imported_bundles (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    payload bytea NOT NULL CHECK (octet_length(payload) <= 1048576),
    digest text NOT NULL,
    PRIMARY KEY(scope_id,root_id),
    FOREIGN KEY(scope_id,root_id) REFERENCES research_staging.import_operations,
    CHECK (digest=encode(sha256(convert_to('retained-artifact-bundle-prototype/1','UTF8') || decode('00','hex') || payload),'hex'))
);
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2,3,4,5));
UPDATE research_staging.schema_version SET version=5;
