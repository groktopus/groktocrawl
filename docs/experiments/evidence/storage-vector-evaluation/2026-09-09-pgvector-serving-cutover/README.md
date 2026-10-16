# Application pgvector serving cutover and rollback

This packet records the first application-level serving rehearsal on the isolated
`inference.example.internal` home-lab pilot. It used only deterministic `pilot.invalid` documents.
Production and the inherited mainline stack were not changed.

## Result

The bounded rehearsal passed its cutover and rollback gates:

- Qdrant and pgvector returned the same six ranked results, with scores equal
  after rounding to five decimal places, and both reported 285 documents.
- A pgvector-served write was immediately searchable and remained absent after
  deletion.
- With Qdrant stopped, pgvector kept serving the pinned search unchanged. New
  writes were rejected with HTTP 503 because the rollback copy could not be
  maintained.
- With PostgreSQL stopped, vector search returned HTTP 503 and health reported
  pgvector unavailable. Switching back to Qdrant restored the pinned response
  exactly while PostgreSQL remained offline.
- After both stores were healthy, 240 writes, 240 immediate searches, and 120
  steady searches completed without HTTP failures in pgvector serving mode.
- All 40 retained-artifact database tests passed in 20.700 seconds while that
  vector workload used the same PostgreSQL instance.

The first Qdrant-outage attempt exposed an unhandled write failure. PR
[#235](https://github.com/magnus919/groktocrawl-x/pull/235) changed it to a stable,
retryable HTTP 503 and added a regression test. The complete outage sequence was
then repeated against merge `5739674` and passed.

## Observed bounds

The sustained mixed phase completed in 30.408 seconds. Index latency was 251.587
ms median and 303.218 ms p95; immediate search latency was 245.222 ms median and
306.626 ms p95. The steady search phase completed in 7.664 seconds with 252.690
ms median and 296.177 ms p95. These are one-host experimental measurements, not
production capacity claims.

The PostgreSQL container was limited to one CPU and 1 GiB. The final point sample
showed 4.02% CPU and 30.46 MiB for PostgreSQL, 0.20% CPU and 22.36 MiB for Qdrant,
and 0.22% CPU and 1.059 GiB for the semantic service. A final sample does not
describe peaks.

## Versions and interpretation

- application merge: `5739674`
- semantic image: `sha256:14f94ea577f2678511fd0034b7e988b0e93cd843de7765734f9f31962f9da082`
- PostgreSQL: 17.11; pgvector: 0.8.6
- PostgreSQL image: `pgvector/pgvector:pg17` at `sha256:17a06c0a60bf6fb548a8493b8f6057dc830b791bbbe1f1ab706ca4b9d97c8180`
- Qdrant: 1.18.2 at `sha256:e13294053db80229932ca53f6ace97c3da1ae2581b770373e187a15707244eb5`

This evidence supports review of ADR-0079 and a bounded rollback window in the
experimental fork. It does not authorize removal of Qdrant, change production,
or claim that this fork replaces mainline. The health body correctly reported
`status: starting` during PostgreSQL loss, while the HTTP health response remained
200; operators must currently inspect the body rather than use status code alone.
Issue [#237](https://github.com/magnus919/groktocrawl-x/issues/237) tracks a
separate liveness/readiness contract and provider-loss recovery check.

## Packet contents

`raw/` contains the synthetic workload, reconciliation reports, health and API
responses, sustained workload results, retained-test output, and the point-in-time
resource sample. Failed setup attempts were excluded; the documented initial
application failure is preserved by PR #235 and its regression test.
