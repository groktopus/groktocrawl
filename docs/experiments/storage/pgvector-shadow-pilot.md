# pgvector shadow pilot

- Status: first isolated run complete; extension recommended before cutover
- Tracking: [issue #227](https://github.com/magnus919/groktocrawl-x/issues/227)
- Parent evaluation: [issue #201](https://github.com/magnus919/groktocrawl-x/issues/201)
- Authority during the pilot: Qdrant

This pilot measures the optional pgvector path through the real semantic-service
index and search boundaries. It does not serve pgvector results, remove Qdrant,
or authorize a production or mainline migration.

The first run is preserved in the
[2026-09-09 application shadow packet](../evidence/storage-vector-evaluation/2026-09-09-application-shadow-pilot/).
It passed response-equivalence, fail-open, reconciliation, and bounded
shared-database correctness checks. Two ordinary comparisons also exposed the
expected lag from asynchronous shadow writes. Keep Qdrant authoritative while
that lag is measured under sustained mixed traffic. The follow-up
[sustained lag packet](../evidence/storage-vector-evaluation/2026-09-09-shadow-lag-sustained/)
recorded 360 matching comparisons, bounded all 240 shadow writes within 75 ms,
and restored exact parity after an outage. It supports the reversible cutover
design proposed in [ADR-0079](../../adr/experimental/groktocrawl-x/0079-consolidate-retained-and-vector-storage-in-postgresql.md).

## Preconditions

Use an experimental deployment and a PostgreSQL role that owns a dedicated
database or schema. Install the `vector` extension before the run if the role
cannot create extensions. Record the image digest, Qdrant collection, active
named vector, embedding model and dimension, PostgreSQL version, pgvector
version, host limits, and test time window.

Pause index mutations for the initial reconciliation. The Qdrant scroll API is
not a point-in-time snapshot; concurrent writes can make a single pass internally
inconsistent. Normal traffic can resume after the tool reports `"parity": true`.

Enable shadow mode only on the experimental semantic service:

```dotenv
VECTOR_STORE_MODE=shadow_pgvector
PGVECTOR_SHADOW_DSN=postgresql://USER:PASSWORD@HOST:5432/DATABASE
PGVECTOR_SHADOW_SAMPLE_RATE=0.1
PGVECTOR_SHADOW_SCORE_TOLERANCE=0.0001
PGVECTOR_SHADOW_TIMEOUT_SECONDS=2
PGVECTOR_SHADOW_BACKFILL_BATCH_SIZE=256
```

Keep the DSN in the deployment secret store. Do not commit it or include it in
the evidence packet.

## Initial reconciliation

Run the tool inside the semantic-service image so it uses the same adapter and
locked dependencies as request traffic:

```bash
python -m shadow_backfill > pgvector-shadow-backfill.json
```

The output contains counts, elapsed time, an ID-set digest, and a parity result;
it contains no page URLs, document content, or credentials. A nonzero exit or
`"parity": false` blocks the pilot. The tool is safe to rerun: it upserts current
Qdrant points and marks shadow rows absent from Qdrant as deleted.

## Application traffic

Run the pinned index, batch-index, delete, and search workload twice: first with
`VECTOR_STORE_MODE=qdrant`, then with `shadow_pgvector`. Keep the corpus, request
order, concurrency, model, and resource limits identical. Capture:

- served status and response digests from Qdrant;
- `groktocrawl_pgvector_shadow_operations_total` by operation and outcome;
- `groktocrawl_pgvector_shadow_comparisons_total` by outcome;
- `groktocrawl_pgvector_shadow_score_delta`;
- semantic-service and database CPU, memory, connection count, and latency;
- a second reconciliation result after traffic stops.

The comparison path hashes the query transiently for stable sampling and does
not retain query text. Mismatch logs contain point IDs only.

## Failure and interference checks

Repeat a bounded slice while PostgreSQL is unavailable, slow enough to exceed
the configured statement timeout, and returning a deliberately altered result.
Every Qdrant response must remain identical to the Qdrant-only control. Shadow
failures and mismatches must appear in metrics, and a reconciliation rerun must
repair missed writes and deletions.

For the shared-database check, run the existing retained-artifact workload at
the same time as shadow indexing and searches. Record both workloads separately.
Do not infer consolidation savings from two isolated databases.

## Decision gates

The pilot passes its correctness gate only when:

- both reconciliation passes report exact active-ID parity;
- Qdrant responses and error behavior match the Qdrant-only control;
- shadow outages do not create served-request failures;
- deletes remain deleted after reconciliation; and
- no credential or source content appears in retained evidence.

Report exact-match frequency, score deltas, latency, resource use, and shared
database interference without converting small samples into capacity claims.
Any correctness failure retains Qdrant. If correctness passes but operational
results are unstable or the sample is too small, extend the pilot. Preparing a
cutover requires a reviewed ADR, an explicit serving adapter, a dual-write repair
strategy, and a rollback rehearsal; this pilot alone cannot select pgvector.
