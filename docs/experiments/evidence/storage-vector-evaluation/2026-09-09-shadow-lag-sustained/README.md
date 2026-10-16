# Sustained pgvector shadow-lag evidence

This packet measures the lag that appeared during the first application shadow
pilot. It ran through the real semantic-service boundary on the isolated
`inference.example.internal` home-lab deployment. Qdrant served every result, and the corpus used
only deterministic `pilot.invalid` documents.

## Result

All 360 normal comparisons matched: 240 searches issued immediately after writes
and 120 searches issued after the shadow backlog drained. All 240 writes and 360
searches returned successfully. The largest sampled backlog was one operation,
and the final drain took 1.344 ms.

The new content-free telemetry measured authority-to-shadow completion delay:

| Bound | Completed writes | Share |
|---|---:|---:|
| 5 ms or less | 196 / 240 | 81.67% |
| 10 ms or less | 228 / 240 | 95.00% |
| 25 ms or less | 237 / 240 | 98.75% |
| 50 ms or less | 239 / 240 | 99.58% |
| 75 ms or less | 240 / 240 | 100.00% |

Mean completion delay was 4.921 ms. The histogram does not retain URLs, query
text, document content, or individual request identities.

## Shared PostgreSQL observation

Forty retained-artifact tests passed in 22.183 seconds while the vector workload
used the same PostgreSQL database. Twelve samples observed PostgreSQL at
4.66–44.28% CPU, 181.1–184.0 MiB memory, and two to three connections under its
1 CPU/1 GiB limit. The semantic process had no container CPU or memory limit, so
these measurements describe this host run rather than production sizing.

The mixed vector phase lasted 35.884 seconds. Index latency was 285.209 ms median
and 423.086 ms p95; search latency was 281.834 ms median and 391.279 ms p95. CPU
embedding inference dominated those application latencies. They are not database
service-level objectives.

## Outage and recovery

Stopping the isolated PostgreSQL container produced one shadow-search failure and
one shadow-write failure. The served Qdrant search returned the same status and
response digest as its pre-outage control. After PostgreSQL restarted, the
reconciliation pass reported 279 authoritative IDs, 279 active shadow IDs, and
exact parity.

## Decision

The combined provider, migration, application, failure, and shared-database
evidence now supports designing a reversible experimental cutover to pgvector.
It does not authorize removing Qdrant from production or deleting a Qdrant volume.
The cutover must retain Qdrant as a rollback target until response parity,
deletion continuity, recovery, and rollback have passed through the application
boundary.

[`manifest.json`](manifest.json) pins the run, [`result.json`](result.json) records
the conclusion, and [`raw/`](raw/) contains the sanitized workload and outputs.
