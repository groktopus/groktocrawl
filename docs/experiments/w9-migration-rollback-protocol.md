# W9 migration and rollback rehearsal protocol

Status: **frozen before execution**

## Question and decision

Can the isolated replacement candidate make PostgreSQL authoritative for retained
research and use pgvector for semantic serving while preserving a complete,
tested route back to its Qdrant rollback copy?

Passing permits the bounded operational pilot. It does not remove Qdrant,
authorize a production cutover, or establish production-scale recovery.

## Scope and authority

The rehearsal runs only against `groktocrawl-x-candidate` on `inference.example.internal`.
PostgreSQL is authoritative for retained research artifacts. Its pgvector table
is a rebuildable serving projection. Qdrant remains the rollback vector copy.
The incumbent deployment and its data are untouched.

The candidate image revision, embedding model, schema version, and a six-document
synthetic corpus are frozen in the receipt. Synthetic documents use `.invalid`
URLs and contain no private data. Existing candidate research histories are
included in the PostgreSQL manifest without exposing their bodies.

## Sequence

1. Record service health, PostgreSQL schema/model identity, retained-artifact
   counts and digests, deletion counts, and both vector-store manifests.
2. Write the six-document corpus through the semantic API, including one update,
   and verify immediate pgvector reads and Qdrant/pgvector ID parity.
3. Delete one document through the API and verify it is absent from both stores.
4. Restart semantic serving and verify the pinned query and counts survive.
5. Create a logical PostgreSQL backup, restore it into a new scratch database,
   and compare schema, retained-artifact, deletion, vector, and model manifests.
6. Recreate only the semantic service in Qdrant mode, time recovery to readiness,
   and verify the pinned query, count, and mutation state against the frozen
   pre-rollback observation.
7. Return semantic serving to pgvector, prove readiness and the pinned query, and
   retain Qdrant unchanged for the operational rollback window.

## Gates

- All expected counts and ID/content digests reconcile with zero unexplained
  differences.
- The five active synthetic documents rank with the same IDs in both providers
  within the declared score tolerance; the deleted ID is absent from both.
- The pinned query remains available after restart, during Qdrant rollback, and
  after returning to pgvector.
- The restored PostgreSQL database matches every declared manifest from the
  source backup, including tombstones and schema/model identity.
- Rollback reaches readiness within 120 seconds. Any uncertain authority,
  partial backup, restore mismatch, or unavailable rollback copy fails closed.
- The final candidate state is pgvector serving with a healthy retained Qdrant
  rollback copy. No store or volume is deleted.

## Evidence and limits

The durable receipt contains counts, hashes, status, elapsed times, versions, and
failure messages. It excludes credentials, connection strings, artifact bodies,
query prose, and container identifiers. The logical dump remains temporary and
is deleted after verification.

This is one bounded home-lab rehearsal with synthetic vector data and the
candidate's current retained histories. It does not prove physical disaster
recovery, point-in-time recovery, production volume, geographic failure, or a
completed rollback window.
