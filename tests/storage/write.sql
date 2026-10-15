-- This fixed schema is probe-owned. Fail if it already exists, never overwrite.
BEGIN;
CREATE SCHEMA storage_transport_probe;
CREATE TABLE storage_transport_probe.root (
    scope_id text NOT NULL,
    root_id uuid NOT NULL DEFAULT gen_random_uuid(),
    PRIMARY KEY (scope_id, root_id)
);
CREATE TABLE storage_transport_probe.evidence (
    scope_id text NOT NULL,
    root_id uuid NOT NULL,
    body bytea NOT NULL,
    digest bytea NOT NULL,
    PRIMARY KEY (scope_id, root_id),
    FOREIGN KEY (scope_id, root_id)
        REFERENCES storage_transport_probe.root(scope_id, root_id),
    CHECK (octet_length(body) <= 1048576),
    CHECK (digest = sha256(body))
);
INSERT INTO storage_transport_probe.root(scope_id) VALUES ('fixture-owner');
INSERT INTO storage_transport_probe.evidence
SELECT scope_id, root_id, decode('00c3a9f09f98800a', 'hex'),
       sha256(decode('00c3a9f09f98800a', 'hex'))
FROM storage_transport_probe.root;
COMMIT;

-- The next connection must see committed bytes, not this rolled-back overwrite.
BEGIN;
UPDATE storage_transport_probe.evidence SET body = ''::bytea, digest = sha256(''::bytea);
ROLLBACK;

DO $$
BEGIN
    BEGIN
        INSERT INTO storage_transport_probe.evidence
        SELECT 'other-scope', root_id, body, digest FROM storage_transport_probe.evidence;
        RAISE EXCEPTION 'Cross-scope reference was accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    BEGIN
        UPDATE storage_transport_probe.evidence SET digest = sha256('different'::bytea);
        RAISE EXCEPTION 'Digest mismatch was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;
SELECT 'write, rollback, scoped FK and digest constraint probes passed' AS result;
