ALTER TABLE research_staging.roots DROP CONSTRAINT roots_revision_format_check;
ALTER TABLE research_staging.roots ADD CHECK (revision_format IN ('structure','research','consolidated'));
ALTER TABLE research_staging.roots ADD COLUMN current_consolidated uuid;
CREATE TABLE research_staging.consolidated_operations (
 scope_id uuid NOT NULL, root_id uuid NOT NULL, operation_id uuid NOT NULL DEFAULT gen_random_uuid(),
 generation bigint NOT NULL, context_digest text NOT NULL,
 reserved bigint NOT NULL CHECK (reserved > 0 AND reserved <= 5242880),
 state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','committed','cancelled')),
 input_digest text,
 PRIMARY KEY(scope_id,root_id,operation_id),
 FOREIGN KEY(scope_id,root_id) REFERENCES research_staging.roots,
 CHECK ((state='committed') = (input_digest IS NOT NULL))
);
CREATE TABLE research_staging.consolidated_publications (
 scope_id uuid NOT NULL, root_id uuid NOT NULL, operation_id uuid NOT NULL,
 knowledge bytea NOT NULL, knowledge_digest text NOT NULL,
 manifest bytea NOT NULL, manifest_digest text NOT NULL,
 summary bytea NOT NULL, analysis bytea NOT NULL, dossier bytea NOT NULL,
 fixture_only boolean NOT NULL CHECK (fixture_only),
 PRIMARY KEY(scope_id,root_id,operation_id),
 FOREIGN KEY(scope_id,root_id,operation_id) REFERENCES research_staging.consolidated_operations,
 CHECK (octet_length(knowledge) <= 1048576 AND octet_length(manifest) <= 1048576),
 CHECK (octet_length(summary) <= 1048576 AND octet_length(analysis) <= 1048576 AND octet_length(dossier) <= 1048576),
 CHECK (knowledge_digest=encode(sha256(convert_to('checked-knowledge-prototype/1','UTF8') || decode('00','hex') || knowledge),'hex')),
 CHECK (manifest_digest=encode(sha256(convert_to('render-manifest-prototype/1','UTF8') || decode('00','hex') || manifest),'hex'))
);
CREATE TABLE research_staging.consolidated_sources (
 scope_id uuid NOT NULL, root_id uuid NOT NULL, operation_id uuid NOT NULL,
 logical_id text NOT NULL CHECK (length(logical_id) BETWEEN 1 AND 200), snapshot_id uuid NOT NULL,
 PRIMARY KEY(scope_id,root_id,operation_id,logical_id),
 FOREIGN KEY(scope_id,root_id,operation_id) REFERENCES research_staging.consolidated_publications ON DELETE CASCADE,
 FOREIGN KEY(scope_id,root_id,snapshot_id) REFERENCES research_staging.snapshots
);
ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version ADD CHECK (version IN (1,2,3,4,5,6,7,8,9,10));
UPDATE research_staging.schema_version SET version=10;
