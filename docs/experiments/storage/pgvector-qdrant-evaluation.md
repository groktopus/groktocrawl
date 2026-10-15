# PostgreSQL + pgvector versus Qdrant evaluation

Status: **evaluation plan only; no production storage change and no Qdrant removal decision**.

The experimental architecture is considering one PostgreSQL authority for evidence and metadata. If that direction is accepted, the vector index should be tested as part of the same decision. The current stack uses Qdrant behind `semantic-svc` for embeddings and similarity search, while Valkey-backed records remain the artifact authority for the inherited research-memory path. This document defines a reversible comparison; it does not assume that PostgreSQL should replace Qdrant.

## Candidates and constants

| Candidate | Vector/index role | Evidence authority during the test |
|---|---|---|
| Qdrant reference | Existing Qdrant collection and distance/index settings | Existing fixture artifact records |
| PostgreSQL + pgvector | PostgreSQL extension with an equivalent vector column, distance operator and metadata filters | The same fixture artifact records |

Both candidates must receive identical embedding bytes, model identity, dimensions, normalization, source IDs, lineage IDs, tenant/scope fields, deletion state, expiry timestamps and top-k requests. Embedding generation and reranking stay outside this storage comparison. The comparison uses a pinned synthetic corpus first; it must not read or mutate production collections.

## Workloads

Run each workload in cold and warm conditions with the same host limits and reset procedure:

1. bulk insert with metadata and duplicate/idempotent replays;
2. top-k similarity search at several result limits and score thresholds;
3. mixed concurrent search and insert traffic;
4. scope-filtered search with foreign-scope negative controls;
5. expiry and deletion propagation, including an orphaned-index reference;
6. restart, index rebuild and backup/restore rehearsal;
7. model/dimension migration with old and new vectors present; and
8. failure injection during write, commit acknowledgement and cleanup.

The workload manifest must pin corpus and embedding hashes, vector dimension, distance metric, filter fields, row/point counts, concurrency, request mix, timeouts, cache state, randomization seed and stop conditions. Every workload keeps its full failure record; timeouts and malformed responses are failed trials.

## First harness

The opt-in Compose file [`compose.vector-evaluation.yml`](../../../compose.vector-evaluation.yml)
starts a private PostgreSQL+pgvector service and a separate Qdrant service on
loopback-only ports. It is intentionally a different Compose project from the
application stack. With Docker Compose v2 and a private password:

```sh
export VECTOR_EVAL_POSTGRES_PASSWORD='use-a-private-value'
docker compose -f compose.vector-evaluation.yml --profile vector-eval up -d --wait
python scripts/run_vector_store_evaluation.py \
  --qdrant-url http://127.0.0.1:16333 \
  --postgres-dsn 'postgresql://vector_eval:use-a-private-value@127.0.0.1:15432/vector_eval' \
  --allow-isolated-database \
  --cleanup \
  --output /tmp/groktocrawl-x-vector-evaluation.json
docker compose -f compose.vector-evaluation.yml --profile vector-eval down
```

The harness uses a synthetic 3-dimensional corpus, a unique collection/table
per run, filtered searches for two scopes, duplicate replay and a soft-delete
check. It records provider failures instead of retrying them. The output is an
evaluation artifact only: it does not select a backend, alter the inherited
Qdrant collection, or claim production performance. By default it runs one
round. `--rounds N` repeats the workload against the same initialized provider
resources, preserving each round in the artifact and aggregating latency and
gate results. The first round follows provider initialization; later rounds
are warm passes and must not be presented as cold-start measurements.

The fork also provides a manual [`Vector Store Evaluation`](../../../.github/workflows/vector-store-evaluation.yml)
workflow. Run it from the repository Actions page when a hosted Docker runner
is available; it runs the harness inside the private Compose network (so the
comparison does not depend on runner host-port behavior), uploads the JSON
packet, and fails closed if either candidate has provider errors or violates a
fixture gate. The workflow defaults to three repeated warm rounds; override the
`rounds` input when a different bounded sample is needed. Set
`fresh_each_round=true` for repeated provider initialization; that mode
recreates the isolated collection and table before every round and still does
not model restart or recovery. The optional `concurrency_workers` and
`concurrency_operations` inputs run a bounded mixed search/idempotent-upsert
workload with one provider client per worker; they are incompatible with fresh
rounds and remain disabled by default. Review that packet before proposing an
ADR change.

The separate manual [`Vector Store Restart Evaluation`](../../../.github/workflows/vector-store-restart-evaluation.yml)
workflow seeds both isolated providers, restarts their containers, reconnects
without recreating the collection or table, and verifies retrieval plus
deletion continuity. It is restart evidence only; it does not replace the
backup/restore rehearsal or establish recovery authority.

The manual [`Vector Store Backup Evaluation`](../../../.github/workflows/vector-store-backup-evaluation.yml)
workflow creates a Qdrant collection snapshot and a PostgreSQL custom-format
`pg_dump`, restores each into fresh isolated services, applies the checked-in
deletion authority manifest, and verifies retrieval and deletion continuity.
It records backup bytes and hashes, restore timing, the manifest hash, and
provider failures as evidence. A successful rehearsal proves only that this
fixture can be recovered with the tested procedure; it does not authorize
production migration or removal of Qdrant.

The successful hosted rehearsal is preserved in the [fresh-target backup/restore
evidence packet](../evidence/storage-vector-evaluation/2026-09-08-backup-restore/).

The deterministic [failure-injection fixture packet](../evidence/storage-vector-evaluation/2026-09-08-failure-injection/)
checks the fail-closed contract for write timeouts, partial writes, malformed
search results, interrupted restore/migration, and deletion acknowledgement
timeouts. It is provider-independent contract evidence only; hosted replay
against isolated Qdrant and pgvector services remains required.

The manual [`Vector Store Fault Evaluation`](../../../.github/workflows/vector-store-fault-evaluation.yml)
workflow replays the same six scenarios around each real isolated provider,
retains provider errors and raw JSON evidence, and fails closed if a provider
reports success after an injected failure. It is opt-in, destroys only its
private evaluation resources, and does not change the application stack.

The first successful provider replay is preserved in the [2026-09-08 evidence
packet](../evidence/storage-vector-evaluation/2026-09-08-provider-fault/). Both
Qdrant and pgvector passed all six bounded scenarios. This closes only the
failure-replay sub-gate; scale/footprint, sustained-load, provider migration/
rollback and reversible-cutover evidence remain open.

The manual [`Vector Store Scale Evaluation`](../../../.github/workflows/vector-store-scale-evaluation.yml)
workflow is the next gate. It runs deterministic 100/500/1000-record fixture
sizes with four workers and 80 mixed operations per provider, records p50/p95/p99
latency and errors, and captures a container resource snapshot. It is a
component-level isolated benchmark: the sizes and workload are evidence inputs,
not production demand or capacity targets.

The first successful hosted run is preserved in the [2026-09-08 scale evidence
packet](../evidence/storage-vector-evaluation/2026-09-08-scale-r3/). Both
providers passed the corrected correctness gates and completed 240 mixed
operations without a recorded failure. The packet is a pass with gaps: the
current Qdrant bulk path is batched while the pgvector bulk path commits rows
individually, and the resource capture is one post-run snapshot. Equivalent
write batching, representative dimensions and scale, sustained-load resource
series, migration/rollback, and reversible cutover remain open.

The follow-up [transaction-batched packet](../evidence/storage-vector-evaluation/2026-09-08-scale-batched-r1/)
also passed every correctness gate and reduced pgvector bulk-write time by
putting each record set in one transaction. It remains an intermediate result:
PostgreSQL `executemany` issues repeated statements while Qdrant receives one
native batch, the run was not repeated, and isolated tail measurements varied.
A PostgreSQL-native bulk path and repeated rounds remain required before using
write or tail-latency measurements in the decision.

The [three-round native-bulk packet](../evidence/storage-vector-evaluation/2026-09-08-scale-native-repeated-r1/)
closes those two measurement-shape gaps for the bounded fixture. Both providers
passed all correctness gates and 720 mixed operations without a failure. Qdrant
had lower bulk-upsert latency in every round; pgvector had lower filtered-search
latency in every round. The packet still uses only synthetic 3-dimensional
vectors, at most 1,000 records and short mixed workloads. Representative scale,
sustained resource measurements, migration/rollback and cutover remain open.

The follow-up [larger 1,024-dimensional packet](../evidence/storage-vector-evaluation/2026-09-09-scale-1024d-larger-r1/)
uses the inherited embedding shape, equal 1,000-record batches, and up to 10,000
records. All rounds passed correctness without provider failures. At 10,000
records Qdrant had lower median ingestion time, filtered-search p50 and higher
mixed throughput, while pgvector had the tighter search p95. The short mixed
workload and final-only resource snapshot do not establish sustained capacity.

The [sustained workload packet](../evidence/storage-vector-evaluation/2026-09-09-scale-1024d-sustained-r2/)
extends the 10,000-record case to 5,000 mixed operations in each of three rounds
and captures phase-attributed container samples. Both providers completed 15,000
operations without a failure. Qdrant throughput was steadier and its sampled
memory was higher; pgvector showed lower median p50 latency but wider throughput
and tail-latency variation. The sequential 33-98 second component phases remain
too short and narrow to establish production capacity.

The manual [`Vector Store Migration Evaluation`](../../../.github/workflows/vector-store-migration-evaluation.yml)
rehearses an expand/cutover/rollback sequence in isolated provider namespaces.
It creates parallel three- and four-dimensional generations, preserves deletion
state during backfill and coexistence, validates scoped retrieval before and
after cutover, and routes back to the source generation. Qdrant uses atomic
collection-alias updates; PostgreSQL uses a transactional route record. This is
an evaluation mechanism only and does not change the application storage path.
The first hosted rehearsal is preserved in the [migration evidence packet](../evidence/storage-vector-evaluation/2026-09-09-migration-r1/).
Both providers passed target-schema, reconciliation, cutover, deletion-continuity,
and rollback gates. Application-level shadow operation and the final operational
surface decision remain open.

The [operational-surface comparison](vector-operational-surface.md) finds that
pgvector simplifies the deployment only if PostgreSQL is also adopted as the
artifact authority. It recommends an experimental shadow-pgvector pilot while
Qdrant remains authoritative, with shared-database interference and application-
level reconciliation as the next gates. The first
[application shadow packet](../evidence/storage-vector-evaluation/2026-09-09-application-shadow-pilot/)
passed response-equivalence, fail-open, exact reconciliation, and bounded
shared-database correctness checks. It also measured two normal mismatches from
asynchronous write lag. The evaluation therefore continues with Qdrant as the
authority until sustained mixed traffic quantifies that lag and a reviewed ADR
defines any serving cutover. The follow-up
[sustained lag packet](../evidence/storage-vector-evaluation/2026-09-09-shadow-lag-sustained/)
recorded 360 matching normal comparisons, bounded all shadow write completion
within 75 ms, passed 40 concurrent retained-artifact tests, preserved the served
response during a PostgreSQL outage, and reconciled exact parity afterward.
[ADR-0079](../../adr/experimental/groktocrawl-x/0079-consolidate-retained-and-vector-storage-in-postgresql.md)
therefore proposes a reversible experimental pgvector cutover with Qdrant kept as
the rollback target until the application rehearsal passes.

## Measures and gates

Record p50/p95/p99 latency, throughput, error and timeout rate, index-build/rebuild time, CPU/RAM/disk footprint, backup size, restore time, and cleanup lag. For retrieval, report exact top-k identity overlap, score ordering changes, recall against a separately computed brute-force reference on the fixture corpus, and scope/deletion correctness. Do not treat Qdrant as truth merely because it is the incumbent.

A candidate can replace the current index only if all of these are satisfied:

- no tested cross-scope result, stale/deleted result, duplicate committed effect or dangling evidence reference;
- retrieval parity and any quality difference are reported with fixed denominators and uncertainty, not a single average;
- cold and warm performance bounds are frozen before the comparison and include rebuild, backup and restore costs;
- model/dimension migration and rollback are demonstrated; and
- removing Qdrant actually reduces operational surface without weakening recovery, retention, deletion or incident response.

If any gate is missing or inconclusive, retain Qdrant and document the measured reason. A PostgreSQL decision must be a new reviewed ADR or an explicit update to the proposed storage ADR; it must not be inferred from structural tests or a successful local boot.

## Execution boundary

This plan is intentionally separate from the W1 quality comparison. It can be prepared with deterministic fixtures before the held-out packet is frozen, but no provider-backed quality conclusion, production migration, volume deletion or Qdrant shutdown is authorized by this document. The first implementation step is a local fixture harness that can run both candidates against the same manifest and emit sanitized evidence.
