# Provider-backed vector failure replay — 2026-09-08

Run: [GitHub Actions 34265631135](https://github.com/magnus919/groktocrawl-x/actions/runs/34265631135)

Status: **bounded isolated provider evidence**.

The manual fault-evaluation workflow replayed the six failure scenarios from
the provider-independent contract packet against fresh, private Qdrant and
PostgreSQL + pgvector resources:

- write timeout before commit acknowledgement;
- partial write requiring reconciliation;
- malformed search response rejection;
- interrupted restore before activation;
- interrupted migration before cutover; and
- deletion acknowledgement timeout.

All six gates passed for both providers. Failed operations were not reported as
successful, malformed results were rejected, partial writes remained visible as
reconciliation cases, and the active resource stayed unchanged after the
interrupted staging scenarios. The raw provider packets and their SHA-256
digests are listed in [`summary.json`](summary.json).

This is still bounded evidence. The run does not measure representative scale,
footprint, sustained load, provider-native migration compatibility, or a real
cutover. It does not select pgvector, retain Qdrant by decision, remove Qdrant,
or change production. Those gates remain open in [issue #201](https://github.com/magnus919/groktocrawl-x/issues/201).
