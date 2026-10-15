# Transaction-batched vector scale evaluation: run 34279376598

This packet preserves the first hosted scale run after PostgreSQL writes were
moved from one autocommit per row into one transaction per record set. It is a
small component benchmark, not production capacity evidence or a storage
selection.

## Verdict

Both providers passed every correctness gate at 100, 500, and 1,000 records.
Each completed 240 mixed operations without a recorded failure.

The transaction change reduced the measured pgvector bulk-upsert time compared
with the preceding run:

| Size | Previous pgvector | Transaction-batched pgvector | Qdrant in this run |
|---:|---:|---:|---:|
| 100 | 73.14 ms | 36.03 ms | 5.07 ms |
| 500 | 225.08 ms | 52.09 ms | 9.20 ms |
| 1,000 | 435.45 ms | 193.47 ms | 15.89 ms |

This narrows one measurement gap but does not make the write implementations
identical. PostgreSQL now has one commit boundary, but `executemany` still sends
repeated statements; Qdrant receives one native point batch. A provider decision
still requires a PostgreSQL-native bulk path such as COPY or a multi-row insert,
plus repeated rounds at representative dimensions and scale.

Filtered reads remained faster in the pgvector prototype on this tiny fixture.
At 1,000 records, filtered-search p50/p95 was 0.25/0.85 ms for pgvector and
1.39/1.71 ms for Qdrant. The mixed workload also recorded zero failures for both
providers. Single-run tail values varied sharply at 500 records, including a
76.19 ms Qdrant search p99 and a 80.00 ms pgvector upsert p99. That variability
is another reason to require repeated rounds rather than compare isolated tail
measurements.

The container snapshot was captured after the workload: PostgreSQL used 27.65
MiB and Qdrant used 85.95 MiB. It is not a peak or trend measurement and cannot
support capacity, soak, or cost claims.

## Remaining gates

- use a PostgreSQL-native bulk path with a comparable wire-level operation;
- repeat cold and warm rounds and report distributions;
- use representative vector dimensions, corpus size, and request mix;
- capture resource time series and sustained-load behavior;
- demonstrate model/dimension migration and rollback; and
- rehearse reversible cutover and deletion continuity.

## Files

- `qdrant.json`: raw Qdrant result
- `pgvector.json`: raw PostgreSQL + pgvector result
- `docker-stats.txt`: post-run container snapshot
- `summary.json`: compact machine-readable verdict and limitations
