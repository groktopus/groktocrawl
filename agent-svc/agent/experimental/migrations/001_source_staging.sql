-- Initial, explicit installation into a new experimental namespace only.
-- Apply transactionally; existing namespaces must fail rather than be overwritten.
CREATE SCHEMA research_staging;
CREATE TABLE research_staging.schema_version (version integer PRIMARY KEY CHECK (version = 1));
INSERT INTO research_staging.schema_version VALUES (1);
CREATE TABLE research_staging.scopes (
    scope_id uuid PRIMARY KEY,
    quota bigint NOT NULL CHECK (quota > 0),
    charged bigint NOT NULL DEFAULT 0 CHECK (charged >= 0 AND charged <= quota)
);
CREATE TABLE research_staging.roots (
    scope_id uuid NOT NULL REFERENCES research_staging.scopes,
    root_id uuid NOT NULL DEFAULT gen_random_uuid(),
    generation bigint NOT NULL DEFAULT 1 CHECK (generation > 0),
    deleted boolean NOT NULL DEFAULT false,
    deleted_at timestamptz,
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
    quota bigint NOT NULL CHECK (quota > 0 AND quota <= 104857600),
    charged bigint NOT NULL DEFAULT 0 CHECK (charged >= 0 AND charged <= quota),
    PRIMARY KEY (scope_id, root_id)
);
CREATE TABLE research_staging.blobs (
    scope_id uuid NOT NULL REFERENCES research_staging.scopes,
    digest text NOT NULL,
    body bytea NOT NULL CHECK (octet_length(body) <= 10485760),
    PRIMARY KEY (scope_id, digest),
    CHECK (digest = encode(sha256(body), 'hex'))
);
CREATE TABLE research_staging.snapshots (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    snapshot_id uuid NOT NULL DEFAULT gen_random_uuid(),
    body_digest text NOT NULL,
    descriptor bytea NOT NULL CHECK (octet_length(descriptor) <= 1048576),
    descriptor_digest text NOT NULL,
    PRIMARY KEY (scope_id, root_id, snapshot_id),
    FOREIGN KEY (scope_id, root_id) REFERENCES research_staging.roots,
    FOREIGN KEY (scope_id, body_digest) REFERENCES research_staging.blobs,
    CHECK (descriptor_digest = encode(sha256(
        convert_to('source-staging/1', 'UTF8') || decode('00', 'hex') || descriptor
    ), 'hex'))
);
CREATE TABLE research_staging.operations (
    scope_id uuid NOT NULL,
    root_id uuid NOT NULL,
    operation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    generation bigint NOT NULL,
    reserved bigint NOT NULL CHECK (reserved > 0 AND reserved <= 11534336),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'committed', 'cancelled')),
    input_digest text,
    snapshot_id uuid,
    PRIMARY KEY (scope_id, root_id, operation_id),
    FOREIGN KEY (scope_id, root_id) REFERENCES research_staging.roots,
    CHECK ((state = 'committed') = (input_digest IS NOT NULL AND snapshot_id IS NOT NULL))
);
CREATE INDEX snapshots_blob ON research_staging.snapshots(scope_id, body_digest);
