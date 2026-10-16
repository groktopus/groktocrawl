# Research-memory pgvector migration

Research-memory artifacts remain in Valkey. This runbook moves only the
semantic vector index for the existing `memory:{id}:data` records. The default
is still Qdrant. The semantic page index has a separate Qdrant/pgvector
projection and uses `VECTOR_STORE_MODE`; do not treat its backfill as a
research-memory migration.

## Modes and state

`RESEARCH_MEMORY_VECTOR_MODE=qdrant` is the current default: Valkey stores full
artifacts and Qdrant stores their embeddings. `shadow_pgvector` keeps Qdrant
reads authoritative and mirrors new writes to PostgreSQL. `pgvector` reads and
writes vectors in PostgreSQL without contacting Qdrant. The PostgreSQL
projection stores the same UUID, query, user scope, compatibility fingerprint,
timestamps, and 1024-dimensional embedding; artifact JSON, freshness, and TTL
remain governed by the existing Valkey record.

PostgreSQL is available through the `durable-research` Compose profile. The
Compose service supplies a protected password file and sets
`RESEARCH_MEMORY_PGVECTOR_DSN` to the profile's database. The default profile
and backend remain unchanged unless the operator selects a PostgreSQL mode.

## Prepare and backfill

1. Back up the Valkey dataset and Qdrant `research_memory` collection. Verify
   the backups and retain them through the rollback window. Do not delete or
   recreate the source collection.
2. Start the additive PostgreSQL service and verify it is healthy:

   ```sh
   docker compose --profile durable-research up -d research-postgres
   docker compose --profile durable-research exec -T research-postgres \
     pg_isready -U groktocrawl -d groktocrawl
   ```

3. Start agent-svc with the existing `qdrant` mode first. Initialize the
   idempotent, independently versioned `research_memory` schema, then copy the
   live source vectors. Backfill only reads Valkey and Qdrant and writes the
   PostgreSQL projection:

   ```sh
   docker compose --profile durable-research exec -T agent-svc \
     python -m agent.research_memory_migration init
   docker compose --profile durable-research exec -T agent-svc \
     python -m agent.research_memory_migration backfill
   ```

4. Set `RESEARCH_MEMORY_VECTOR_MODE=shadow_pgvector` and recreate only
   `agent-svc`. This leaves Qdrant reads authoritative and mirrors new writes.
   Rerun the idempotent backfill and verify while writes are paused or during a
   quiet window; if parity fails because records changed during the scan,
   reconcile again and repeat the verification:

   ```sh
   docker compose --profile durable-research up -d --no-deps agent-svc
   docker compose --profile durable-research exec -T agent-svc \
     python -m agent.research_memory_migration backfill
   docker compose --profile durable-research exec -T agent-svc \
     python -m agent.research_memory_migration verify
   ```

   Require `parity: true`, zero missing/extra IDs, zero metadata mismatches,
   and zero vector mismatches. Expired Qdrant points and points whose Valkey
   artifact has already expired are counted as skipped. The command reports
   counts only and does not print query text, artifact bodies, or credentials.

## Cutover and rollback

After verification, set `RESEARCH_MEMORY_VECTOR_MODE=pgvector` and recreate
only `agent-svc`. A PostgreSQL error is reported as a miss for lookup or a
failed store; the service does not silently fall back to stale Qdrant data.
Monitor application errors and lookup outcome metrics. Keep PostgreSQL and
Qdrant data intact until the rollback window closes.

To return reads to Qdrant after PostgreSQL-only writes, first restore the
current non-expired projection to Qdrant, then set the mode back to `qdrant`:

```sh
docker compose --profile durable-research exec -T agent-svc \
  python -m agent.research_memory_migration restore-qdrant
```

The restore command upserts rows only when their Valkey artifact still exists
and has not expired. It does not delete unrelated Qdrant points or change
Valkey. Verify the reported restored count, then switch the environment and
recreate agent-svc. A full Qdrant collection restore from the retained backup
remains the recovery path for corruption; application rollback does not
require restoring database or Valkey files.

For the semantic page index, `VECTOR_STORE_MODE=pgvector` is the rollback-copy
mode: it serves from PostgreSQL and continues Qdrant writes. The new
`pgvector_only` mode removes Qdrant from readiness and request handling. If
rolling back after using it, pause semantic index writes and snapshot Qdrant
first. Run `python /app/shadow_backfill.py restore-qdrant` in semantic-svc to
upsert the active PostgreSQL records without deleting any Qdrant points. This
reports nonmatching Qdrant IDs and exits with parity false while stale points
remain. Only after confirming the backup and complete PG projection, run
`python /app/shadow_backfill.py restore-qdrant --prune-stale`; this explicitly
deletes Qdrant IDs absent from the active PostgreSQL set and verifies the exact
ID-set parity by reading Qdrant back. Retain the database projection and verify
parity before switching to `qdrant`.

To run semantic-svc with PostgreSQL as its only vector service, set
`VECTOR_STORE_MODE=pgvector_only` in the operator environment and use the
dedicated Compose profile without activating `indexing` or `durable-research`:

```sh
docker compose --profile pgvector-only up -d research-postgres semantic-svc
docker compose --profile pgvector-only exec -T semantic-svc \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8003/ready").read().decode())'
```

That profile enables the semantic service and its PostgreSQL dependency while
leaving the Qdrant profile inactive. Existing Qdrant data and any already
running Qdrant container remain untouched for rollback. The standard
`indexing` and `durable-research` profiles keep Qdrant enabled for legacy and
rollback-copy modes.
