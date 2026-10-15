-- Durable authority for complete experimental research artifact sets.
CREATE TABLE research_staging.research_artifact_sets (
    scope_id uuid NOT NULL REFERENCES research_staging.scopes,
    research_id uuid NOT NULL,
    run_id uuid NOT NULL,
    artifact_set_id uuid NOT NULL,
    manifest bytea,
    manifest_digest text NOT NULL CHECK (length(manifest_digest) = 64),
    set_digest text NOT NULL CHECK (length(set_digest) = 64),
    total_bytes bigint NOT NULL CHECK (total_bytes BETWEEN 1 AND 33554432),
    deleted boolean NOT NULL DEFAULT false,
    committed_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '30 days',
    PRIMARY KEY (scope_id, research_id),
    UNIQUE (scope_id, run_id),
    UNIQUE (scope_id, artifact_set_id),
    CHECK ((NOT deleted AND manifest IS NOT NULL) OR (deleted AND manifest IS NULL)),
    CHECK (manifest IS NULL OR octet_length(manifest) <= 1048576)
);

CREATE TABLE research_staging.research_artifacts (
    scope_id uuid NOT NULL,
    research_id uuid NOT NULL,
    artifact_id text NOT NULL CHECK (length(artifact_id) BETWEEN 1 AND 200),
    layer text NOT NULL CHECK (layer IN ('summary', 'analysis', 'dossier')),
    body bytea NOT NULL CHECK (octet_length(body) <= 10485760),
    content_digest text NOT NULL CHECK (length(content_digest) = 64),
    PRIMARY KEY (scope_id, research_id, artifact_id),
    UNIQUE (scope_id, research_id, layer),
    FOREIGN KEY (scope_id, research_id)
        REFERENCES research_staging.research_artifact_sets ON DELETE CASCADE
);

ALTER TABLE research_staging.schema_version DROP CONSTRAINT schema_version_version_check;
ALTER TABLE research_staging.schema_version
    ADD CHECK (version IN (1,2,3,4,5,6,7,8,9,10,11,12,13));
UPDATE research_staging.schema_version SET version=13;
