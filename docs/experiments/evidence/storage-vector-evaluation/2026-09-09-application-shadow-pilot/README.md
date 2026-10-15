# Application-level pgvector shadow pilot

This packet records the first live application-level shadow run on the isolated
`inference.example.internal` home-lab deployment. It used synthetic `pilot.invalid` documents.
Qdrant served every response; PostgreSQL never became a serving dependency.

## Result

The correctness and fail-open gates passed. This is enough to keep evaluating a
PostgreSQL-only storage stack, but it is not enough to remove Qdrant.

- All six pinned control operations returned the same HTTP status and response
  digest in Qdrant-only and shadow modes.
- Initial, post-outage, and final reconciliation reported exact active-ID parity.
  The final pass found 38 active points in each store.
- Indexing and search still succeeded while PostgreSQL was stopped. The missed
  shadow write was restored by reconciliation.
- A 12-second exclusive PostgreSQL lock did not delay or alter the served search:
  its response digest was identical and latency was 52 ms versus 87 ms for the
  unlocked control.
- Hiding every synthetic shadow row forced a recorded mismatch while the served
  Qdrant response remained byte-for-byte identical. Reconciliation restored
  parity.
- Forty retained-artifact database tests passed while 30 indexes and 30 searches
  ran against the same PostgreSQL database. The vector workload had no failures.

The ordinary shadow traffic produced 32 matches and two mismatches across 34
successful comparisons. Those mismatches occurred in immediate write-then-search
traffic because shadow writes run asynchronously. They did not affect callers,
and reconciliation restored exact parity. A cutover design must quantify and
bound this lag rather than assuming synchronous equivalence.

## Shared-database observation

The retained-artifact suite ran for 18.687 seconds. The vector workload overlapped
it for 4.102 seconds. Five descriptive samples observed PostgreSQL at 15.28–34.91%
CPU, 181.2–183.2 MiB memory, and two to three connections under its 1 CPU/1 GiB
limit. The sample proves coexistence and basic correctness only; it does not
establish sustained capacity or production sizing.

During the overlapping vector slice, index latency was 65.710 ms median and
99.061 ms p95. Search latency was 61.384 ms median and 87.739 ms p95. These are
small CPU-inference samples and should not be used as service-level objectives.

## Decision

Keep Qdrant authoritative and extend the shadow pilot. The next storage decision
should add explicit shadow-lag measurements under sustained mixed load, then
write and review the cutover ADR only if those measurements remain acceptable.
Production and mainline are unchanged.

The pinned environment is in [`manifest.json`](manifest.json), the machine-readable
conclusion is in [`result.json`](result.json), and the sanitized command outputs
are retained under [`raw/`](raw/). No credentials, source text, or production
content are included.
