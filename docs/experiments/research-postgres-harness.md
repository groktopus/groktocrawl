# Isolated PostgreSQL exploration harness

This is an experimental groktocrawl-x infrastructure slice under accepted
[ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).
It is not a mainline replacement, an application storage API, a final schema,
or a W3 lifecycle acceptance result.

## Run the isolated probes

Requires Docker Engine and Docker Compose v2 with `up --wait`. Select the file
explicitly; do not combine it with the inherited deployment. From the repo root:

```sh
umask 077
openssl rand -hex 32 > /tmp/groktocrawl-x-research-password
export RESEARCH_POSTGRES_PASSWORD_FILE=/tmp/groktocrawl-x-research-password
export RESEARCH_PROBE_UID=$(id -u)
export COMPOSE_FILE=compose.research-storage.yml
export COMPOSE_PROFILES=storage
export COMPOSE_PROJECT_NAME=groktocrawl-x-research-probe

docker compose config --quiet
docker compose config --services
docker compose up -d --wait research-postgres
docker compose run --rm storage-probe write
docker compose run --rm storage-probe read
docker compose up -d --force-recreate --wait research-postgres
docker compose run --rm storage-probe read
docker compose run --rm storage-probe cleanup
docker compose down --timeout 30
```

Use a dedicated credential file; keep it for subsequent starts of this database.
The official image reads the password at initial database creation, so generating
a new file does not rotate an existing database's password. The probe accepts
hexadecimal passwords to avoid pgpass escaping ambiguity. It creates a temporary
mode-0600 pgpass file inside its own container and removes it on exit. The probe
runs as the password file owner UID, preserving mode-0600 host secret permissions
without adding capabilities. Set `RESEARCH_PROBE_UID` to that owner on subsequent
runs as well. File-backed Compose secrets retain host ownership on Linux.

The selected project owns its private network and named volume. No ports are
published and no inherited services are attached. `down` preserves the volume;
there is no automatic volume deletion. Keep the password file private and outside
Git. The database owner role is for this isolated harness only; application
permissions and server-derived scope authorization remain separate work.

## What the probes establish

`write.sql` creates the explicitly named `storage_transport_probe` schema and
commits synthetic bytes containing NUL, multibyte Unicode and a newline. It
checks rejection of a cross-scope composite foreign key and an invalid byte
digest, then rolls back a changed body. `read.sql` uses an independent connection
and a consistent read transaction to verify the original exact bytes and count.
CI repeats that read after replacing the database container against its existing
volume. `cleanup.sql` removes only the two synthetic tables and their schema,
without CASCADE. A failed write leaves its diagnostic state; a second write
refuses to overwrite an existing probe schema. Inspect it before explicitly
running cleanup. Never point these probes at an application database.

Runtime CI executes the probe job for every runtime change; Runtime Gate requires
its success alongside the inherited integration and twin checks. Hosted CI logs
record server version and image identity. A skipped, failed or cancelled required
probe blocks that gate. The dedicated hosted runner discards its VM after the
job; the workflow itself preserves the database volume on shutdown.

The schema exists only to probe database transport and constraints. The SQL digest
check uses plain SHA-256 for raw bytes; it is not the schema-prefixed JCS identity
from [canonical admission](research-canonical-admission.md). These probes do not
validate full research references, immutable writes, quotas, root lifecycle,
concurrency races, ambiguous commit reconciliation, export/import or restore.
They do not establish access control simply because a composite FK rejects one
invalid reference. Container replacement with the same volume is not a backup
restore or a workflow recovery test.

## Version and next slice

The harness pins `postgres:17.11-bookworm` in both services. PostgreSQL lists 17.11
among its August 2026 maintenance releases; the container follows the official
image's password-file and PostgreSQL 17 data-volume conventions. References:
[PostgreSQL releases](https://www.postgresql.org/docs/release/),
[official image](https://hub.docker.com/_/postgres).
A version tag is reproducible at the PostgreSQL version level, not an immutable
image digest; CI records the resolved image. No extensions are installed.

The [retained-source adapter](research-source-storage.md) adds an explicitly
invoked application transaction path and first migration in a separate experimental
namespace. Its real database tests run in the same required CI job. Preserve server-selected scopes and opaque
roots, generation checks, exact canonical bytes, logical quota accounting and
operation receipts. Then expand real-database coverage against the
[lifecycle matrix](research-storage-lifecycle.md). Full W3 acceptance and a pilot
still require the remaining lifecycle, concurrency and isolated restore evidence.
pgvector remains a consolidation candidate; Qdrant removal is a separate gate.

The required job also runs the [source restore rehearsal](research-source-restore.md)
against a second, quarantined database in the isolated cluster.
