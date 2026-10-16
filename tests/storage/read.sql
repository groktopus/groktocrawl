BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
DO $$
DECLARE
    matched integer;
BEGIN
    SELECT count(*) INTO matched FROM storage_transport_probe.evidence
    WHERE scope_id = 'fixture-owner'
      AND body = decode('00c3a9f09f98800a', 'hex')
      AND octet_length(body) = 8
      AND digest = sha256(body);
    IF matched <> 1 OR (SELECT count(*) FROM storage_transport_probe.evidence) <> 1 THEN
        RAISE EXCEPTION 'Exact-byte reopen failed';
    END IF;
END $$;
SELECT current_setting('server_version') AS server_version,
       'exact-byte reopen passed' AS result;
COMMIT;
