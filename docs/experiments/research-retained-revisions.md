# Retained structural revisions

`RevisionStore` extends the experimental PostgreSQL source store with bounded,
immutable structural revisions and a relational source-reference ledger. This
implements another slice of [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md),
using the existing [ADR-0069](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md)
structural validator. It does not publish a final research answer, perform semantic
verification, adopt a runtime or freeze the complete `knowledge-ir/1` format.

## Commit and reopen

A trusted server first retains source snapshots through `SourceStore`. It then
calls `reserve_revision(scope, root, generation, expected_parent, maximum_bytes)`.
The database issues an opaque revision UUID and reserves capacity. Construction
occurs outside the transaction, using this server-issued identity.

The input is a strict, canonicalized `retained-structure-prototype/1` envelope:

- `schema_version`: this prototype envelope version.
- `parent_revision_id`: canonical UUID text, or null for the first revision.
- `structure`: an existing `knowledge-structure-prototype/1` record.

The embedded structure must match the trusted scope, research root and reserved
revision identity. Its snapshot IDs must identify actual retained snapshots in
that root. Existing structural rules check entity uniqueness, evidence locator
bounds, exact quote equality, claim relationships and acyclic derivations. The
whole envelope is JCS with a schema-prefixed SHA-256 digest; existing fixture and
verifier serialization/hashes are unchanged.

`commit_revision` checks the active root/generation, reservation, expected current
parent and quota. It compares each structure snapshot's exact UTF-8 body, digest,
URL and normalization with the scoped retained source and canonical descriptor.
Caller-recorded retrieval dates/media metadata are structural declarations, not
new authenticated acquisition provenance. Then it atomically inserts the canonical
revision, derives/inserts its relational source references, commits the receipt,
releases unused capacity and advances the current pointer. Foreign keys bind scope,
root, parent and snapshot identities. Only one of competing same-parent revisions
can advance the pointer; the losing reservation must be cancelled or retried with
a newly constructed revision.

`read_revision` uses a consistent snapshot and checks lifecycle, canonical bytes,
digest, embedded parent, exact equality of JSON snapshot references and the SQL
ledger, and all retained source bodies/descriptors. It can reopen older retained
revisions without a session. The API returns a structural revision, not a verified
or human-approved answer. There is no new public endpoint or inherited runtime wiring.

## Identity, receipts and bounds

All retained revisions in this root (maximum twenty) participate in immutable
entity comparison. Reusing a snapshot/evidence/claim/relationship ID with different
record content or kind fails, even if the ID disappeared in an intermediate
revision. New entities need new IDs. This does not implement the full fixture
history's introduction/predecessor annotations or the final Knowledge IR's complete
provenance/verification schema.

The envelope is at most 1 MiB. The existing structure validator additionally bounds
snapshot count, text and graph sizes. Its inline source text remains in the envelope
as well as retained blobs; the canonical payload bytes are charged per revision,
in addition to source charges. Twenty revisions bound transaction-time history
validation; this is a prototype limit, not a throughput claim. Metadata/index/WAL
footprint is still separate from logical quota.

`revision_receipt` returns the committed input digest for reconciliation after an
uncertain commit outcome. Repeating the same revision/input is idempotent; changed
input fails. The receipt survives root deletion as metadata, without payload text.
`cancel_revision` releases a pending reservation once. A committed revision is not
undone by cancellation. Successful reservations and commits renew the staging
window; reads, receipts and cancellation do not. No automatically resumed jobs or
request-idempotent reservation API is added.

SourceStore deletion on schema 2 first clears the current pointer and removes all
revision payloads/reference ledgers, then purges snapshots/unshared source blobs,
releases total charges, cancels pending source/revision reservations and retains
tombstone/receipt metadata. Thus copied quotes in revision payloads are removed as
well as source bodies. The same scope-then-root lock order serializes these writes.
This remains a trusted server API, not protection against arbitrary database-owner SQL.

## Migration and restore

SourceStore's explicit initial installer still creates schema 1. `migrate_revisions`
is a separate explicit, forward-only migration to schema 2. It locks the schema
version table, requires version 1 and applies `002_structure_revisions.sql` in one
transaction. Reapplication or unknown version fails. This code's source operations
read schemas 1/2/3/4; revision operations require version 2, 3 or 4. An older source
binary that only accepts version 1 rejects the upgraded schema rather than silently
operating on it. There is no automatic upgrade or destructive down migration.

The required isolated CI job takes a schema-1 dump before migration, runs the actual
revision contracts on schema 2, restores the old dump into a new private database,
and compares exact source-identity/descriptor/body-digest manifests. It then runs
the [post-backup deletion rehearsal](research-source-restore.md) on schema 2, now
including revisions in both the retained and subsequently deleted control roots.
Restored live revisions must pass their canonical/source-ledger checks. These are
bounded fixtures in one isolated cluster, not a production migration/RPO/RTO claim.

To reproduce, follow the exact `Back up schema one and exercise retained revisions`
and subsequent restore steps in [Runtime CI](../../.github/workflows/runtime.yml)
with the explicitly selected isolated Compose project. The `revision` phase runs
seven real database cases: reopen/replay, competing parent and changed entity,
wrong source root/body and corrupted ledger, deletion/late writer, lost commit ACK
and reservation bound, removed/reintroduced identity, and migration refusal. It
requires a populated schema-1 test database and a verified pre-migration backup;
never drop an existing database to force the fixture to rerun.

The [fixture publication extension](research-retained-publications.md) now stores
synthetic verification records and three audited output layers on schema 3. Full
authenticated research verification, export/import and the remaining lifecycle/
admission/restore gates are still future work.
This slice does not complete W2/W3 or select pgvector over Qdrant.
