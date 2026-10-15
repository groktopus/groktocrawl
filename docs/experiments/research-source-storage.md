# Experimental retained-source staging

The opt-in `SourceStore` in `agent/experimental/source_store.py` implements the
source-staging portion of [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).
It does not publish IR revisions or final research answers. No inherited API,
session, worker or default deployment invokes it. This is groktocrawl-x exploration,
not a replacement for mainline or a production storage adoption.

## Implemented boundary

A trusted server establishes the scope UUID and constructs `SourceStore` using
operator-controlled libpq connection settings. Scope IDs and connection strings
are not public request parameters. `provision_scope` is an administrative action;
`create_root` returns a database-generated UUID and generation starts at 1.

1. Call `reserve(scope, root, generation, size)` before source acquisition. The
   returned operation UUID reserves logical capacity and owns one source write.
2. Acquire at most the reserved capacity outside all database transactions. The
   acquisition owner must bound its buffer and cancel the reservation on failure.
3. Call `commit_source` with that operation, exact UTF-8 bytes and source URL.
   Admission validates bytes and builds the canonical descriptor before locking.
   One transaction inserts/reuses the scope-local blob, inserts the snapshot,
   commits its receipt and releases unused reservation capacity.
4. Reopen with `read_source` from any new adapter instance. It requires an active,
   unexpired root and revalidates exact descriptor bytes, canonical digest and
   the descriptor's body reference against the actual stored body.

`source-staging/1` has exactly `schema_version`, `url`, `normalization` and
`body_sha256`. `utf8-exact/1` preserves the supplied UTF-8 bytes, including CR/LF,
NUL and Unicode composition. No normalization is applied after admission. Source
URL is acquisition metadata, not proof of provenance; the adapter fetches no URLs.
The descriptor uses the existing schema-prefixed JCS digest. Raw body blobs use
plain SHA-256. These are prototype source descriptors, not `knowledge-ir/1`.

Application operations never update stored blob or snapshot content. Blob reuse
compares actual bytes as well as the digest. PostgreSQL constraints bind scoped
references and byte digests; reads additionally compare canonical field content.
The database owner can still alter tables: this is an internal trusted API, not a
SQL role-isolation or hostile-administrator security boundary. Public authentication,
authorization, per-service database roles and error mapping remain future work.

## Transactions, capacity and lifecycle

All mutations lock the scope row before the root row under explicit Read Committed.
Reads use Repeatable Read. No provider, acquisition or indexing call occurs under
a database lock. Each call owns a new connection, with 10-second connection and
statement limits, a 3-second lock timeout, a 10-second idle-transaction timeout and
a 30-second outer deadline. The [shared admission guard](research-storage-admission.md) now bounds aggregate
connections within a process, failing immediately before connecting when its
eight default slots are occupied. It is not a pool or distributed limiter; caller
dispatch and acquisition buffers still need their own bounds.

The prototype caps a body at 10 MiB and a reservation at 11 MiB (body plus bounded
canonical descriptor). Root quota is at most 100 MiB and scope quota at most 1 GiB;
smaller quotas support isolated tests. Charges include pending reservation bytes
and each committed snapshot's body plus descriptor bytes. Repeated content remains
charged per snapshot even when its physical blob deduplicates. Metadata/index/WAL
footprint is additional and not measured by logical quota. No capacity benchmark
or storage SLO is established here.

Successful new reservations/commits renew the 24-hour staging deadline. Reads,
receipt lookup and cancellation do not renew it. Expiry denies normal reads/writes;
there is no automatic expiry sweeper yet. `cancel_reservation` releases a pending
reservation once and never undoes a committed snapshot.

`delete_root` locks in the same order, removes this root's snapshots, removes
scope-local blobs only if no snapshot still references them, releases charges,
cancels pending operations, marks the root deleted and advances generation in one
transaction. A later write cannot recreate the root. Shared bodies remain available
to other roots until their last reference is removed. A tombstone and minimal
operation metadata remain; this slice has no tombstone/receipt pruning job. The
bounded deletion is atomic rather than a production-scale background purge.

## Retry receipts and ambiguous outcomes

Reusing an operation with identical input returns its existing snapshot ID;
changed input fails. `receipt` returns snapshot ID, input digest and generation,
including after root deletion, but no source bytes. Compare its input digest with
the expected descriptor before accepting a reconciled result. Receipt metadata
does not authorize reading a deleted snapshot.

An exception around COMMIT may mean the write committed. Reopen and inspect the
receipt before retrying the same operation. Cancellation/timeouts do not promise
rollback if COMMIT already ran. Reserving capacity itself is not request-idempotent:
if its acknowledgement is lost, it may leave charged staging capacity until an
owner cancels it or deletes the root. There is no automatic retry/recovery worker,
lease ownership, webhook or completed-job event in this slice.

## Installation and real database tests

`install()` explicitly applies `001_source_staging.sql` in one transaction to a
NEW `research_staging` namespace. It refuses an existing namespace without modifying
its contents. Normal source operations accept schema versions 1 through 9; there is no automatic
migration at import or service startup. This is the first forward migration only.
Future changes to retained data require a reviewed migration, pre-migration backup
and restore rehearsal. Do not use the test installer on a pilot or existing database.

Follow the [isolated PostgreSQL harness](research-postgres-harness.md) setup,
including its private password file, `RESEARCH_PROBE_UID`, Compose file/profile
and dedicated project name. Then, on a fresh isolated database:

```sh
docker compose up -d --wait research-postgres
docker compose run --build --rm storage-adapter
docker compose down --timeout 30
```

The adapter image builds the service code from checkout, using pinned
`psycopg[binary]==3.3.5`. The explicit unittest runner installs the namespace once
and exercises eleven real-database cases: exact reopen/replay, lost post-commit ACK,
wrong scope/root/generation, reservation rejection/cancellation, competing root
quotas, competing scope quotas, shared-blob deletion, commit/delete interleaving,
expiry, descriptor-reference corruption and refusal to reinstall. The ACK test
injects an exception after an actual successful transaction; it is not a network
partition test. The race test accepts either serialized ordering; it does not
claim exhaustive interleaving coverage. Test rows remain in the dedicated database;
a repeat installation refuses them. Use a new isolated test project for a fresh
run, retaining old volumes unless their deletion is separately authorized.

Runtime CI requires this adapter test step within PostgreSQL Storage Probes.
Local admission tests run without a database; the real database cases are never
substituted with mocks or silently skipped. Source references for the driver:
[Psycopg async connections](https://www.psycopg.org/psycopg3/docs/advanced/async.html),
[transaction contexts](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

## Remaining W3 work

The isolated prototype now retains structural revisions, audited fixture publications,
historical re-renders and bounded export/import bundles with reference ledgers.
Required CI includes backup restore and post-backup deletion reconciliation for
these fixture artifacts. Complete Knowledge IR compatibility, scheduled expiry
collection, reservation reconciliation, aggregate acquisition admission, measured
capacity and the remaining adversarial/production recovery coverage are still
required by the [lifecycle matrix](research-storage-lifecycle.md).
[Bounded expiry collection](research-expiry-collection.md) now supplies an explicit
internal method; actual race/restore CI remains required and scheduling is absent. No W3 completion, restart-safe
job execution, human calibration or pgvector/Qdrant adoption follows from these tests.

A [bounded source restore rehearsal](research-source-restore.md) now exercises a
schema-1 logical dump/restore and post-backup deletion reconciliation in required
CI. It does not complete the full artifact or production recovery gates above.

The [retained revision extension](research-retained-revisions.md) adds an explicit
schema-2 migration. This version of SourceStore supports source operations on
schema 1 through 9 and removes dependent revision/publication payloads during root deletion.
The [fixture publication extension](research-retained-publications.md) adds schema 3,
audited fixture outputs and thirty-day published-root retention.

The [scoped import extension](research-import-contract.md) adds schema 5. Native
writes reject import-only roots. Root deletion coordinates with grant/commit and
purges the origin's bounded recipient copies with their quota charges in the same
transaction. This is an internal isolated prototype, not public scope authorization.

Schema 7 adds [complete-history roots](research-complete-history-storage.md). Source
staging remains available, and root purge removes complete-history ledgers before
source payloads. Legacy structural roots are not converted.
