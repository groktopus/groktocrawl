# Bounded vector-store scale evaluation: run 34275141380

This packet preserves the first successful hosted scale run for both isolated
vector-store candidates. It is component-level evidence from a small synthetic
corpus. It does not establish production capacity, cost, or permission to remove
Qdrant.

## Workload

- deterministic 3-dimensional vectors at 100, 500, and 1,000 records;
- four workers and 80 mixed search/upsert operations per provider and size;
- filtered top-k retrieval checked against independently computed cosine scores;
- duplicate-write replay and scope checks at every size; and
- one container resource snapshot after the workload completed.

Both providers passed every correctness gate. Each provider completed all 240
mixed operations with zero recorded failures.

## Observations

| Provider | Size | Bulk upsert | Filtered search p50 / p95 | Mixed throughput | Mixed search p50 / p95 / p99 |
|---|---:|---:|---:|---:|---:|
| Qdrant | 100 | 6.41 ms | 1.59 / 3.25 ms | 493.68 ops/s | 4.35 / 9.39 / 19.76 ms |
| Qdrant | 500 | 13.44 ms | 1.97 / 2.22 ms | 374.03 ops/s | 4.85 / 9.91 / 12.06 ms |
| Qdrant | 1,000 | 24.36 ms | 2.95 / 3.37 ms | 453.31 ops/s | 6.03 / 10.14 / 16.01 ms |
| pgvector | 100 | 73.14 ms | 0.37 / 1.14 ms | 2,811.66 ops/s | 0.45 / 2.18 / 2.87 ms |
| pgvector | 500 | 225.08 ms | 0.44 / 1.09 ms | 2,498.34 ops/s | 0.53 / 1.51 / 3.58 ms |
| pgvector | 1,000 | 435.45 ms | 0.46 / 1.09 ms | 2,665.27 ops/s | 0.58 / 1.76 / 4.01 ms |

The bulk-write measurements are not a fair comparison of backend capability.
The Qdrant path sends one batched upsert, while the pgvector prototype commits
one row at a time. The apparent Qdrant write advantage therefore identifies a
harness asymmetry that must be corrected before write-performance evidence can
inform the storage decision.

The pgvector prototype was faster for filtered reads and the small mixed
workload in this run. That result applies only to this synthetic 3-dimensional,
1,000-record maximum corpus and this runner. It cannot be extrapolated to the
larger embeddings, data volume, traffic mix, or topology of a deployment.

The post-run snapshot recorded PostgreSQL at 5.98% CPU and 27.57 MiB memory and
Qdrant at 0.02% CPU and 84.78 MiB memory. This is one point after the workload,
not peak utilization or a time series, so it is not footprint, capacity, soak,
or cost evidence.

## Failed runs retained

Two earlier runs remain in the GitHub Actions record because they exposed
incorrect assumptions in the evaluator:

- run `34270233230` required the same ordering within exact score ties; and
- run `34272562343` required one particular record when several records shared
  the score at the top-k cutoff.

The checker now accepts provider-specific ordering inside ties while still
requiring the requested result count, unique IDs, correct scope, independently
verified scores, descending score order, and inclusion of every strictly better
record. Run `34275141380` passed those corrected gates.

## Remaining decision gates

- repeat the comparison with equivalent batched write behavior;
- exercise representative vector dimensions, larger data, and a sustained load;
- collect resource time series and storage growth rather than a final snapshot;
- demonstrate provider-native model/dimension migration and rollback; and
- rehearse reversible cutover and deletion continuity.

## Files

- `qdrant.json`: raw Qdrant result
- `pgvector.json`: raw PostgreSQL + pgvector result
- `docker-stats.txt`: post-run container snapshot
- `summary.json`: compact machine-readable verdict and limitations
