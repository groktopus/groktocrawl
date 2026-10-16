# Mainline durable research and vector-store migration

This runbook adds the durable research PostgreSQL store and, after verified
backfill and parity checks, makes pgvector the vector-serving store in the
existing Compose project. Qdrant remains online and receives writes for read
rollback. The existing Valkey job store, SlopSearX image/configuration, and
API/MCP ports are preserved. This runbook does not enable scripted research
execution. Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false` throughout
the vector migration.
Existing research/workspace/artifact read flags may remain enabled to serve
retained records. Pgvector promotion is independent of research-run admission.
The adoption decision is [ADR-0069](../adr/0069-adopt-bounded-research-substrate.md);
the sequencing and release work are in the
[research architecture migration plan](../plans/research-architecture-migration.md).

Run commands from the deployment checkout that owns the existing Compose
project and `.env`. Do not change the Compose project name, rename the existing
`valkey` or `qdrant` services, replace their volumes, or run `docker compose
down -v`. PostgreSQL gets a new volume named from the project and
`research_postgres_data`.

## Prepare and back up

1. Pull and review the merged mainline release. Confirm the release contains the
   research migrations through schema version 16 and the semantic service's
   `shadow_backfill.py`. Record the deployed agent, semantic, Valkey, Qdrant, and
   SlopSearX image digests. Preserve the existing `.env` and local Compose
   override; don't print either file into logs or paste secrets into commands.
   Remove `GROKTOCRAWL_REVISION` from the deployed `.env` unless an explicit
   operator override is intended: the agent's `env_file` forwards it at runtime
   and would otherwise mask the revision baked into the released image.
2. Create a PostgreSQL password outside the repository. The entrypoint writes a
   `.pgpass` record, so use a 64-character hexadecimal password (no colon or
   backslash), restrict the file to the Compose operator, and set
   `DURABLE_RESEARCH_POSTGRES_PASSWORD_FILE` in `.env` to its path. Example:

   ```sh
   umask 077
   openssl rand -hex 32 > /secure/path/research-postgres-password
   chmod 600 /secure/path/research-postgres-password
   ```

   Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false` and
   `VECTOR_STORE_MODE=qdrant` during preparation. Do not enable fixture-run
   admission at any point in this vector migration. Existing research flags may
   remain enabled when needed for read-only access to previously retained roots
   and artifacts. Leave Valkey DB 0 and Qdrant as they are.
3. Back up the full Valkey dataset (the RDB includes DB 0 and DB 2) and take a
   Qdrant collection snapshot before enabling the new profile. Store the
   resulting files off-host and verify their checksums and restore procedure.

   ```sh
   set -eu
   backup_dir=/secure/path/groktocrawl-backup
   mkdir -p "$backup_dir"
   before=$(docker compose exec -T valkey valkey-cli LASTSAVE | tr -d '\r')
   docker compose exec -T valkey valkey-cli BGSAVE
   until [ "$(docker compose exec -T valkey valkey-cli LASTSAVE | tr -d '\r')" -gt "$before" ]; do sleep 1; done
   docker compose cp valkey:/data/dump.rdb "$backup_dir/valkey-before-research.rdb"
   snapshot_name=$(docker compose --profile indexing exec -T semantic-svc python -c 'from qdrant_client import QdrantClient; client=QdrantClient(url="http://qdrant:6333"); print(client.create_snapshot(collection_name="groktocrawl_pages").name)')
   docker compose cp qdrant:"/qdrant/snapshots/groktocrawl_pages/$snapshot_name" "$backup_dir/"
   shasum -a 256 "$backup_dir"/* > "$backup_dir/SHA256SUMS"
   ```

   The snapshot commands are read-only apart from asking Qdrant to create its
   documented snapshot artifact. They do not copy candidate volumes or replace
   the live mainline data.

## Add PostgreSQL and, if required, candidate research records

4. Render Compose with the production `.env` without displaying the resolved
   configuration, then start only the additive database service:

   ```sh
   docker compose --profile durable-research config --quiet
   docker compose --profile durable-research up -d research-postgres
   docker compose --profile durable-research exec -T research-postgres \
     psql -U groktocrawl -d groktocrawl -Atc \
     'select version from research_staging.schema_version'
   ```

   Require exactly schema version 16 and a healthy PostgreSQL service before
   proceeding. Fresh volumes initialize from the checked-in SQL files. The
   PostgreSQL image only runs `/docker-entrypoint-initdb.d` for a new data
   directory; later upgrades use the application migrations and must advance
   versions without reinitializing or replacing the volume.
5. If durable research records from the isolated candidate must move to
   mainline, take a consistent source dump of only `research_staging` after
   stopping new candidate writes. Transfer the dump through the operator's
   approved secure channel. Do not copy the candidate PostgreSQL data directory,
   candidate Valkey volume, or candidate Qdrant rollback volume.

   ```sh
   # On the candidate host; writes a protected dump in the operator's backup directory.
   docker compose -f compose.experimental-candidate.yml exec -T candidate-postgres \
     pg_dump -U groktocrawl_x -d groktocrawl_x --format=custom \
     --data-only --schema=research_staging > /secure/path/research-staging.dump

   # On mainline after verifying target schema version 16 and the dump checksum.
   docker compose --profile durable-research exec -T research-postgres \
     pg_restore -U groktocrawl -d groktocrawl --data-only \
     --schema=research_staging --no-owner --exit-on-error - \
     < /secure/path/research-staging.dump
   ```

   Reconcile counts for every table in `research_staging`, scope/root ownership,
   committed-operation invariants, foreign-key/orphan checks, and artifact
   digests against the source dump before enabling artifact authority. The
   candidate's semantic shadow schema is intentionally excluded; the mainline
   vector index is backfilled from mainline Qdrant below.

   This cross-host dump/restore is a separate, optional data-transfer operation,
   not part of upgrading both existing deployments. The normal two-deployment
   rollout keeps each host's own PostgreSQL, Valkey, and Qdrant state and does
   not copy a live data directory or database between hosts. Use this block
   only when an explicit ownership decision requires candidate research rows on
   the mainline host.

## Backfill, compare, and cut over vectors

6. Set `VECTOR_STORE_MODE=shadow_pgvector` and
   `PGVECTOR_SHADOW_SAMPLE_RATE=1.0` in the operator-managed `.env` before
   starting semantic-svc. The reconciliation command rejects `qdrant` mode;
   in shadow mode Qdrant remains authoritative while PostgreSQL receives
   mirrored writes. Start both profiles so Qdrant, semantic-svc, and PostgreSQL
   remain available together:

   ```sh
   docker compose --profile indexing --profile durable-research up -d \
     qdrant semantic-svc research-postgres
   ```

   Run the repository's Qdrant-to-pgvector reconciliation against the live
   mainline Qdrant collection. This is idempotent and reports source count,
   target count, stale deletions, ID-set digest, and parity; require exit status
   zero and `parity: true`.

   ```sh
   docker compose --profile indexing --profile durable-research run --rm \
     --no-deps semantic-svc python shadow_backfill.py
   ```

7. Recreate only semantic-svc with the shadow settings from step 6 if it was
   already running before the mode change. Qdrant remains authoritative while
   writes are mirrored and searches are compared. Observe a representative production
   window; require zero missing IDs, no unexplained result mismatch above the
   configured score tolerance, no shadow write failures, healthy service
   status, and no material latency/resource regression. Record numeric
   accept/abort thresholds before beginning this comparison window.

   ```sh
   docker compose --profile indexing --profile durable-research up -d \
     --no-deps --force-recreate semantic-svc
   ```

8. Cut over only after the reconciliation and comparison gates pass. Set
   `VECTOR_STORE_MODE=pgvector` and retain Qdrant dual writes. If research
   artifacts are in scope, keep the database schema and read-only artifact
   authority available after the schema-16 and table-level reconciliation
   passes. Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false` so new
   research uses the live adapter rather than demonstration data. Live admission
   additionally requires all four research flags, schema 16, configured search,
   capture and model services, and the release acceptance checks described in
   [ADR-0070](../adr/0070-execute-live-durable-research.md). Existing research
   flags may remain enabled for retained reads during preparation. The separate [isolated demo verification](experimental-replacement-candidate.md#verify-and-freeze-the-receipt)
   persists a true fixture flag only in its dedicated test environment through
   all checkpoints, then resets that file to false and recreates the demo agent;
   do not apply that test opt-in to this production migration.
   Pgvector cutover is independent of research-run admission and must be validated on its own. Recreate the agent and semantic
   services and confirm existing API, MCP, and semantic readiness on their
   existing ports. Monitor search
  result parity, vector counts, PostgreSQL saturation, Qdrant write freshness,
  request errors, and latency for the agreed observation window.

   Record the promotion evidence in the operator change record: schema version
   16; exact Qdrant-to-pgvector ID/content/vector parity with zero mismatches;
   zero missing IDs and shadow-write failures during comparison; and fresh
   Qdrant dual writes. Also record the target immutable image digests and
   healthy readiness of API, MCP, semantic, portal, and parse endpoints. Verify
   SlopSearX, Valkey, and Qdrant container/image/volume identities are unchanged
   after rollout. Do not promote if any result is missing or unexplained.

## Rollback and recovery

- **Vector read rollback:** set `VECTOR_STORE_MODE=qdrant`, keep
  `research-postgres` and its volume intact, then recreate semantic-svc with
  both profiles. The promoted semantic code keeps Qdrant current as the rollback
  store during pgvector serving. Verify readiness reports Qdrant ready and
  representative searches return results. If the current image is defective,
  restore the previously recorded agent/semantic image digests as well.
- **Research API rollback:** scripted fixture-run admission should already be
  disabled by `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false`. If it was
  enabled accidentally, disable it and restore the previous agent image if
  needed. Keep existing
  research flags required to read retained roots until their owners have a
  separate disposition. This leaves existing mainline jobs in Valkey DB 0 and
  research rows in PostgreSQL intact. Do not delete the new database volume as
  part of application rollback.
- **Database recovery:** restore PostgreSQL only when the database itself is
  damaged or inconsistent. Take a forensic copy first. Restore the verified
  `pg_dump` into a new/empty PostgreSQL volume, run schema checks, and point the
  service to it only after validation. A restore can lose writes after the
  selected backup; record the recovery point and owner acceptance before
  switching. Never restore candidate data over the mainline database without a
  fresh target backup.
- **Existing state:** do not roll back by removing or renaming
  `valkey_data`/`qdrant_data`, copying raw live database files, or using
  `docker compose down -v`. Retain both original volumes and the new PostgreSQL
  volume until a separate deprecation decision authorizes cleanup.

## Protected capture boundary

The protected production topology is selected explicitly with
[`docker-compose.protected.yml`](../../docker-compose.protected.yml); the base file remains the local/default HTTP
topology. The overlay keeps SlopSearX and its MCP companion unchanged. It
isolates the scraper worker to the source-capture network and its fixed egress
proxy, moves browser and optional Flare rendering behind their own egress
proxies, routes model traffic through a separate fixed-target broker, and
exposes only the allowlisted scraper ingress to the API and host. Renderer,
Valkey-capability, model-control, and Flare-control paths use named Unix-socket
volumes. Mainline Valkey and Qdrant services and volumes retain their current
names and data. A single `SCRAPER_CAPTURE_EGRESS_PROXY_URL` variable is not a
substitute for the overlay.

Before changing the live stack:

1. Confirm the release workflow published `capture-egress-svc/Dockerfile`,
   `flare-control/Dockerfile`, and `flare-protected/Dockerfile` for the target
   host architecture. Verify the pinned FlareSolverr base digest supports that
   architecture; fail the rollout if the image was published only for another
   platform. Record the exact application image digests.
2. Confirm the host has no route or bridge subnet overlap with the overlay's
   default `10.240.251.0/24` through `10.240.255.0/24` plan. Check both
   `ip route` and all existing Docker network IPAM subnets. The mainline host's
   existing `172.31.0.0/16` Docker network conflicts with the experimental
   overlay's former 172.31 address plan; the protected overlay now defaults to
   the verified-free 10.240 plan. If any host has a conflict, set every
   `PROTECTED_*_SUBNET`, `PROTECTED_*_POOL`, `PROTECTED_*_IP`, and source
   gateway variable in `.env` as a coherent address plan and rerun the
   non-overlap and Compose checks. Source and Flare worker firewalls require
   their selected private peer, subnet, and exact HTTP proxy URL to match;
   changing only an IPAM subnet is unsafe.
3. Render and review the merged model without printing it or exposing the
   resolved environment:

   ```sh
   docker compose -f docker-compose.yml -f docker-compose.protected.yml \
     --profile indexing --profile durable-research --profile protected-flare \
     config --quiet
   ```

   Require `LLM_BASE_URL` to be the intended model endpoint. Keep API keys in
   the operator-managed environment file. This overlay deliberately does not
   pass the complete `.env` into the isolated scraper; it forwards the
   reviewed scraper settings and adapter credentials explicitly. Custom
   `SCRAPER_PROXY_URL`, `SCRAPER_PRIVATE_URL_ALLOWLIST`, and private or loopback
   scrape destinations are unsupported by the protected worker. Its fixed
   capture proxy permits only public destinations on HTTP/HTTPS ports and
   rejects private addresses after DNS resolution. Inventory current scrape,
   browser, and adapter destinations before cutover; private home-LAN
   control-plane endpoints must not become capture exceptions. A private model
   endpoint is a separate reviewed case: grant only its exact hostname through
   `LLM_GATEWAY_PRIVATE_HOSTS`, which applies only to the model broker and does
   not relax source-capture rules.
4. Back up the current Valkey dataset and Qdrant collection as described above.
   Keep the current Compose project name: changing it creates new project-scoped
   Valkey/Qdrant volume names and would make existing data appear missing.
5. Start the protected topology during the approved window. Use an explicit
   service allowlist so neither SlopSearX service can be recreated as a side
   effect. Keep this guard in the same shell as the `up` command; abort if a
   target list is edited to include either search service:

   ```sh
   set -eu
   targets=(valkey qdrant research-postgres semantic-svc scraper-svc \
     scraper-socket-init scraper-ingress-svc scraper-state-control \
     capture-egress-svc browser-renderer-svc browser-svc \
     scraper-model-control model-egress-svc agent-svc mcp-svc \
     flare-renderer-svc flare-control-svc)
   for service in "${targets[@]}"; do
     case "$service" in
       slopsearx|slopsearx-mcp) echo "refusing to recreate search service" >&2; exit 1 ;;
     esac
   done
   docker compose -f docker-compose.yml -f docker-compose.protected.yml \
     --profile indexing --profile durable-research --profile protected-flare \
     up -d --no-deps "${targets[@]}"
   ```

   Confirm all health checks, API and MCP smoke probes, and a representative
   scrape complete. Verify through host inspection that the scraper worker has
   only `capture_source`, the renderer has only its capture network, model
   control has only `model_gateway`, and the host port 8001 is served by the
   allowlisted ingress. Do not infer this from a successful `compose config`.

To return to the standard HTTP deployment, first remove the overlay-only
containers without removing volumes, then recreate the base services. Run from
the same project directory/name:

```sh
docker compose -f docker-compose.yml -f docker-compose.protected.yml \
  --profile protected-flare \
  rm -sf scraper-ingress-svc scraper-state-control scraper-model-control \
    model-egress-svc capture-egress-svc browser-renderer-svc \
    scraper-socket-init flare-renderer-svc flare-control-svc
docker compose -f docker-compose.yml up -d scraper-svc browser-svc agent-svc
```

This restores the base scraper HTTP server on its existing host port and the
legacy browser service. Do not add `-v`; keep the protected socket volumes and
all data volumes until the rollback is verified and a separate cleanup is
approved. If Flare was enabled in the overlay, the old base `flare-solverr`
profile remains available as its own compatibility path. Preserve the new
PostgreSQL volume and any candidate backup regardless of application rollback.
