# Larger 1,024-dimensional vector evaluation: run 34295676281

This packet preserves three paired Qdrant and pgvector rounds at 1,000, 5,000,
and 10,000 records. Vectors use the inherited BGE-M3 shape of 1,024 dimensions,
and both providers receive the same 1,000-record ingestion batches.

## Verdict

All six provider rounds passed every correctness gate. Each provider completed
720 mixed operations across the packet with zero recorded failures.

Median observations across the three rounds were:

| Size | Qdrant ingest | pgvector ingest | Qdrant search p50 / p95 | pgvector search p50 / p95 | Qdrant mixed throughput | pgvector mixed throughput |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 0.87 s | 0.97 s | 3.85 / 4.15 ms | 3.61 / 3.67 ms | 347 ops/s | 410 ops/s |
| 5,000 | 4.33 s | 5.07 s | 7.16 / 14.30 ms | 10.22 / 10.26 ms | 186 ops/s | 110 ops/s |
| 10,000 | 8.92 s | 10.10 s | 12.03 / 48.01 ms | 18.10 / 18.15 ms | 136 ops/s | 58 ops/s |

Qdrant had lower median total ingestion time at all three sizes. At 10,000
records it also had lower median search latency and higher mixed throughput,
while pgvector had the tighter search tail. These observations are bounded to
this runner, corpus generator, filter distribution, 80-operation mixed workload,
and three repetitions. They do not establish production capacity or cost.

The failed predecessor run `34293092845` is retained in GitHub Actions. It
exposed Qdrant's 32 MiB HTTP payload limit when 10,000 vectors were serialized
into one roughly 105 MB request. This successful run applied equal 1,000-record
batches to both providers and recorded total ingestion time across all batches.

The post-run snapshot recorded PostgreSQL at 168.8 MiB and Qdrant at 392.6 MiB.
It was captured after work completed and is not peak or trend evidence.

## Remaining gates

- sustain a declared request mix long enough to assess latency and error trends;
- collect CPU, memory, disk, and network time series during the workload;
- exercise model/dimension migration and rollback with both providers;
- rehearse reversible cutover, deletion continuity, and recovery; and
- assess whether removing Qdrant reduces operational burden without weakening service behavior.

## Files

- `qdrant-round-1.json` through `qdrant-round-3.json`: raw Qdrant results
- `pgvector-round-1.json` through `pgvector-round-3.json`: raw pgvector results
- `docker-stats.txt`: post-run container snapshot
- `summary.json`: compact machine-readable verdict and median observations
