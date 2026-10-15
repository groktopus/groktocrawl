# Vector dimension migration and rollback: run 34309027113

This packet preserves the first provider-backed rehearsal of an expand,
cutover, and rollback sequence for the isolated Qdrant and pgvector candidates.
It transforms the deterministic three-dimensional fixture into a separate
four-dimensional generation by appending a zero coordinate, which preserves
the fixture's cosine relationships while forcing an incompatible schema.

## Verdict

Both providers passed every bounded gate:

- the new dimension and provider-native index were created;
- target retrieval matched the independent scoped reference before cutover;
- retrieval remained correct through the active-route cutover;
- a deletion applied during source/target coexistence remained absent;
- routing returned to the original generation and reconciled successfully; and
- no cross-scope or previously deleted record appeared.

Qdrant switched and rolled back through atomic collection-alias updates.
PostgreSQL switched and rolled back through a transactionally updated route
record. The complete Qdrant rehearsal took 86.23 ms and the pgvector rehearsal
took 32.43 ms on this tiny fixture. Those durations describe control-path work
on one hosted runner and are not migration capacity estimates.

This closes the bounded provider-native dimension migration and rollback check.
It does not exercise the live semantic service, production-sized backfill,
concurrent application writes, or an operator recovery window. It does not
select pgvector, remove Qdrant, or alter the application storage path.

## Remaining decision work

- compare the actual operational surface of one PostgreSQL authority with the
  current PostgreSQL-plus-Qdrant shape;
- define application-level shadow reads/writes and reconciliation for a bounded
  pgvector pilot; and
- record the storage adoption or retention decision in a reviewed ADR.

## Files

- `qdrant.json`: raw Qdrant schema, reconciliation, cutover, deletion, and rollback evidence
- `pgvector.json`: raw pgvector schema, reconciliation, cutover, deletion, and rollback evidence
