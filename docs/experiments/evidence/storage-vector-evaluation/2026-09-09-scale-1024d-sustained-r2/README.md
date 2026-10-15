# Sustained 1,024-dimensional vector evaluation: run 34303200570

This packet preserves three paired Qdrant and pgvector rounds at 10,000
records. Each round ran 5,000 mixed search/write operations after ingestion,
using 1,024-dimensional deterministic vectors and equal 1,000-record batches.
Container CPU and memory were sampled throughout the run, and phase markers
associate samples with each provider round.

## Verdict

All six provider rounds passed every correctness gate. Each provider completed
15,000 mixed operations with zero recorded failures.

Median observations across the three rounds were:

| Observation | Qdrant | pgvector |
|---|---:|---:|
| Ingest 10,000 records | 8.83 s | 10.17 s |
| Mixed search p50 / p95 / p99 | 15.64 / 38.02 / 54.88 ms | 7.44 / 96.97 / 103.38 ms |
| Mixed upsert p50 / p95 / p99 | 9.98 / 25.89 / 51.84 ms | 2.91 / 72.23 / 75.76 ms |
| Mixed throughput | 198 ops/s | 116 ops/s |
| Provider CPU median / sampled maximum | 101.48% / 104.27% | 99.48% / 100.40% |
| Provider memory median / sampled maximum | 240.4 / 380.7 MiB | 128.8 / 189.7 MiB |

Qdrant delivered steadier mixed throughput across the three rounds (197-201
ops/s). pgvector varied from 62 to 322 ops/s, and its p50 search latency varied
from 3.98 to 83.08 ms while its p95 remained between 94.78 and 98.83 ms.
Qdrant used roughly twice the sampled memory, while both providers occupied
about one CPU core during their own phase.

These are bounded component observations, not production capacity or a soak
test. Provider phases ran sequentially for 33-98 seconds, Qdrant always ran
first, Docker sampling was coarse, and the synthetic request mix does not
include the rest of the GroktoCrawl stack. CPU values above 100% reflect Docker's
multi-core accounting. The results support sizing and follow-up design work;
they do not select a provider or justify removing Qdrant.

## Remaining gates

- exercise model and dimension migration plus rollback with both providers;
- rehearse reversible cutover, deletion continuity, backup, and recovery;
- measure representative end-to-end service behavior rather than an isolated
  store adapter; and
- decide whether the operational simplification from pgvector outweighs its
  observed tail latency and variability for this workload.

## Files

- `qdrant-round-1.json` through `qdrant-round-3.json`: raw Qdrant results
- `pgvector-round-1.json` through `pgvector-round-3.json`: raw pgvector results
- `docker-stats-series.csv`: timestamped container resource samples
- `workload-phases.csv`: provider and round start/finish markers
- `docker-stats.txt`: post-run container snapshot
- `summary.json`: compact machine-readable observations and limits
