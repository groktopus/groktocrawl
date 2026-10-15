# Repeated native-bulk vector scale evaluation: run 34287607217

This packet preserves three paired Qdrant and pgvector rounds against fresh
collections/tables. Both providers used one native bulk operation per record
set. The first round began after new service containers were launched; later
rounds reused those service processes but created new resources. These are first
and subsequent rounds, not a production cold/warm model.

## Verdict

All six provider rounds passed every correctness gate. Across the full packet,
each provider completed 720 mixed operations with zero recorded failures.

Bulk upsert favored Qdrant in every round. Filtered-search latency favored the
pgvector prototype in every round. Median results across the three rounds were:

| Size | Qdrant bulk median (range) | pgvector bulk median (range) | Qdrant search p50 median | pgvector search p50 median |
|---:|---:|---:|---:|---:|
| 100 | 4.52 ms (4.09–4.81) | 32.62 ms (30.38–61.03) | 1.06 ms | 0.25 ms |
| 500 | 9.20 ms (8.94–10.90) | 67.93 ms (67.57–72.03) | 1.19 ms | 0.24 ms |
| 1,000 | 16.38 ms (16.29–16.79) | 113.14 ms (112.47–115.57) | 1.34 ms | 0.29 ms |

Mixed throughput was materially more variable, especially for pgvector at
1,000 records, where the three rounds ranged from 554 to 2,060 operations per
second. This short 80-operation workload is too small for a sustained throughput
or tail-latency claim.

The directions above are consistent within this packet, but the workload uses
synthetic 3-dimensional vectors and no more than 1,000 records. Real embedding
dimensions change storage, index, memory, and distance-computation costs. The
packet therefore closes the repeated-round gate only for this bounded fixture;
it does not choose a provider or establish production capacity.

The resource file is one snapshot after all rounds. It is not peak, trend, soak,
or cost evidence.

## Remaining gates

- repeat with the intended embedding dimension and a larger representative corpus;
- use a longer, declared request mix and collect resource time series;
- exercise model/dimension migration and rollback with both providers;
- rehearse reversible cutover, deletion continuity, and recovery; and
- assess the operational savings of removing Qdrant against any measured loss.

## Files

- `qdrant-round-1.json` through `qdrant-round-3.json`: raw Qdrant results
- `pgvector-round-1.json` through `pgvector-round-3.json`: raw pgvector results
- `docker-stats.txt`: post-run container snapshot
- `summary.json`: compact machine-readable verdict and median observations
