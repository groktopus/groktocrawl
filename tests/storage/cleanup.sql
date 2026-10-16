-- Explicitly removes only the two synthetic probe tables, with no CASCADE.
BEGIN;
DROP TABLE storage_transport_probe.evidence;
DROP TABLE storage_transport_probe.root;
DROP SCHEMA storage_transport_probe;
COMMIT;
