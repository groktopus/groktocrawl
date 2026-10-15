# Bounded expiry collection contract

Implementation contract for [issue #68](https://github.com/magnus919/groktocrawl-x/issues/68)
under [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).
**The isolated `ExpiryStore` implements this bounded contract.** Existing readers deny expired
roots; this slice adds physical payload removal and quota release in the isolated
experimental database. It does not complete W3 or adopt a background runtime.

## Internal operation

A trusted server explicitly requests collection for one existing authorized scope,
with a candidate limit from one to twenty. The method selects at most that many
non-deleted roots whose deadlines have elapsed, ordered by expiry and root UUID.
It captures this bounded candidate list once; it does not keep discovering roots
until a scope is empty. Candidates are hints, not permission to delete.

Process each candidate in a separate transaction. Existing 30-second transaction,
SQL and lock timeouts apply to each transaction. The invocation also uses a 60-second total deadline. A timeout or cancellation may leave
previous candidate transactions committed. Do not claim whole-pass rollback, undo
completed purges, or resume automatically. Repeating an explicit pass is safe.
The successful result lists bounded candidate outcomes and purge counts without
source text; errors state that earlier transactions may have committed.

The first API is an internal method, not an HTTP/CLI endpoint or a scheduler.
Caller authorization is required just as for scope provisioning and import grants;
this method does not implement principal authentication or public scope membership.
No provider work, job resumption, webhook or session mutation occurs.

## Transaction and race rules

For each candidate:

1. Acquire the same fixed transaction advisory lock used by imports and deletion.
2. Discover its import dependencies while coordinated. A native origin can have
   at most twenty issued recipient roots; imported roots cannot be new origins.
   Reject an inconsistent dependency set instead of performing unbounded cleanup.
3. Lock all affected scopes in UUID order, then all affected roots in
   `(scope UUID, root UUID)` order, deduplicating both sets. Never hold one root
   while discovering and acquiring another scope lock.
4. Recheck candidate existence, deletion state and current expiry under the locks.
   A root renewed before the collector acquires ownership is skipped. An expired
   root cannot be renewed after collection takes ownership and fences/purges it.
   Do not use the original candidate timestamp as deletion authority.
5. Use the same transactional purge as explicit deletion: remove dependent
   publications, revisions, snapshots, unshared blobs and imported bundle payload;
   cancel pending reservations, release each charge once, mark deleted and advance
   generation. Preserve minimal receipt/operation and tombstone metadata.

Expired native origins purge their pending and committed recipient copies in that
same transaction, even when those copies belong to other scopes. Expired recipient
roots purge only themselves, preserving the origin and other recipients. Rechecking
origin lifecycle on read continues to deny an expired origin before collection runs.
A repeated candidate already purged through another candidate is skipped.

Valid writer activity or explicit publication can renew a still-active native root.
Collection must serialize with those existing scope/root locks. Tests must show
both valid orderings: renewal wins and is preserved, or collection wins and the late
writer fails. Ordinary reads, receipt lookup and replay never renew retention.
Readers that already hold a consistent pre-purge snapshot may finish; subsequent
reads must fail. No immediate erasure of bytes already delivered to a client is claimed.

## Storage and resource boundaries

The explicit forward schema-6 migration adds an index for scope-local live
expiry discovery. Keep readers for retained schemas and restore a private schema-5
backup before calling migration validation complete. No automatic migration or
modification of an already-applied migration file.

One root transaction processes at most twenty-one roots including its dependent
copies. The invocation processes at most twenty initially selected candidates and
has a total deadline. This bounds application work and lock fan-out, not production
throughput or storage capacity. Existing root/scope quotas still apply. Measure the
candidate query plan on the fixture database; do not require an index scan for tiny
tables where a sequential scan can be reasonable.

Charges reflect logical payload/reservations. Removing rows does not promise an
immediate decrease in PostgreSQL files or backup size; vacuum and physical capacity
management are separate operations. Do not use `VACUUM FULL`, drop a database or
delete a volume as part of collection. Keep shared blobs until all surviving
references are gone. Receipt/tombstone reclamation, pending-operation reconciliation
inside otherwise live roots, aggregate connection admission and scheduled cleanup
remain separate work.

## Required CI evidence

Actual PostgreSQL CI must cover:

- Expired staging, publication and import roots are unreadable before collection;
  collection removes their payload and returns quota, preserving metadata receipts.
- Unexpired roots and shared blobs still referenced by surviving roots remain exact.
- A limited pass processes only its bounded candidate set; a repeat does not double
  release charges. Missing scope and invalid limits fail explicitly.
- Renewal versus collection, import grant/commit versus origin collection, and
  explicit deletion versus collection serialize without resurrected copies or
  partial payload. Record tested orderings, not an exhaustive race-proof claim.
- A fault before commit rolls that root back; uncertain commit acknowledgement and
  a later pass reveal a stable tombstone/receipt without duplicate accounting.
  Failure after one completed candidate does not undo that candidate.
- Pre-migration schema-5 source/revision/publication/import manifests survive an
  independent backup restore. Restore a backup predating collection, reconcile
  current deletion history and retention, and prove expired copied evidence stays
  unavailable while retained controls and receipts still resolve.

Update the lifecycle/readiness guides with the actual tested behavior when the
implementation lands. Synthetic tests do not establish semantic correctness,
public authorization, production recovery, scheduling reliability or full W2/W3
acceptance.

## Internal usage and test scope

Apply `ExpiryStore.migrate_expiry()` explicitly to an isolated schema-5 namespace,
then invoke `collect_expired(scope, limit=20)` as a trusted authorized caller. The
method returns immutable `CollectionOutcome` records with candidate root UUID,
`purged`/`skipped` status and number of roots purged, including recipient dependencies.
Exceptions retain their original type and carry a note that earlier candidates may
have committed. There is no scheduled invocation or public route.

The required `expiry` phase runs twenty-nine actual PostgreSQL cases: ten new expiry
cases and nineteen import/export regressions on schema 6. A writer-lock test allows
a staging deadline to elapse while the valid writer owns the lock, lets collection
discover the old expired version, then commits renewal before collector ownership;
the collector must skip it. Additional tests cover a stale hint, expired late
writes, collection/import/deletion contention, shared blobs, bounded passes, quota,
faults before commit and after acknowledgement loss, and partial-pass failure.
These cases are not exhaustive interleavings. The index test inspects the catalog
and a real query plan without demanding a specific tiny-table access strategy.

CI compares source, revision, publication and import manifests from a restored
schema-5 backup. The final schema-6 restore begins from a backup made before expiry
collection, applies the resulting current deletion inventory, and verifies copied
evidence stays denied. Its result records `post_backup_expiry_collection_reconciled`.
No content dump is uploaded. Hosted success is required before merging; local pure
tests alone do not validate these transactions.
