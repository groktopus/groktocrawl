# Retain complete research publications and historical re-renders

Part of [issue #71](https://github.com/magnus919/groktocrawl-x/issues/71), implementing
[complete publication admission](research-complete-publication-admission.md) on top
of [complete history storage](research-complete-history-storage.md). This remains
isolated fixture storage under ADR-0071's accepted exploration scope. It introduces
no production adoption or runtime/recovery architecture decision.

## Schema and lifecycle

Explicit migration 8 requires schema 7 under an exclusive schema-version lock.
Separate `research_publication_operations`, `research_publications` and
`research_publication_sources` tables refer to complete research revisions.
Legacy publication tables and byte formats remain unchanged; old structural roots
are not converted. Schema 8 readers continue supporting the earlier storage paths.

`ResearchPublicationStore.reserve_research_publication` locks scope/root, requires
a live native complete-research root and matching generation, validates the retained
history and source closure, and reserves quota before returning a server-issued
publication UUID. Operation metadata binds context, revision UUID and the complete
canonical revision digest. The existing 1 MiB bound covers canonical publication
bytes and all three rendered outputs together.

Commit validates the envelope outside write locks, then rechecks operation identity,
generation, lifecycle, context and the complete retained ancestry/source closure
under the scope/root lock. Ordinary publication requires that the pinned revision
is still current. If research advanced after reservation, commit fails and the
caller can cancel the unused reservation. Outputs, source ledger, exact charge and
receipt are written in one transaction. A successful new publication sets the
root's publication time and thirty-day retention deadline.

Identical committed replay returns its UUID without extending retention or charging
again. A different canonical input is rejected. Cancellation releases a pending
reservation once and does not reverse committed data. Reservation creation itself
is not request-idempotent. Deletion/expiry purges complete publications before their
revision/source dependencies and preserves receipt metadata. No background expiry
scheduler or restart-safe execution is supplied.

## Historical re-render

Ordinary publication cannot select an old revision after research advances. The
explicit `rerender_of` path requires the original publication UUID and its trusted
original context together. Reservation reopens and verifies that original on the
same scope/root, requires the same full revision digest, and requires unchanged
verification policy/verifier. The new trusted presentation context may choose a
new renderer/auditor.

The operation records its original publication and full revision digest. Commit
can then render that historical revision without moving the root's current research
pointer. Exact research JSON remains bound to the retained revision; it cannot be
rewritten along with new passing audits. Original publication bytes remain intact.
Root deletion/expiry still denies both original and re-rendered outputs. This is a
historical representation, not a claim that evidence has been refreshed.

Reads use one repeatable-read snapshot. They require a live complete root and a
committed operation matching stored revision/context/content digests, reconstruct
the retained history, validate sources and all fixture audits, and compare exact
output bytes and the publication source ledger. Receipts alone do not expose
expired or deleted payloads.

## Validation and remaining scope

The schema-8 database phase has eleven new publication cases and forty existing
complete-history and legacy lifecycle regressions, following 132 earlier cases:
183 total expected. It covers replay/retention, old/current revision selection,
explicit re-render context, revision advancement after reservation, cancellation,
wrong identities/generation/format, source closure, concurrent cancel/commit,
delete/expiry purge, corrupted outputs/ledger, transaction rollback and lost commit
acknowledgement. Actual hosted execution is required before merge; pure fixture
construction is not database evidence.

CI backs up schema 7 before migration, restores it separately and compares source,
legacy revision/publication/import and complete history manifests. Final schema-8
recovery seeds two complete roots with an original publication followed by a newer
revision and a historical re-render. One root is expired after backup. Recovery
requires its deletion inventory, proves the old backup contains both publications,
then denies their access after reconciliation and validates every retained complete
publication/receipt. The result reports `verified_complete_research_publications`.
No database or volume is removed by the added steps.

Complete-history export/import remains open under #71. Legacy bundle methods cannot
export these new publication rows. Public principal authorization, independent
semantic evaluation, human calibration, aggregate concurrency/memory sizing and
production/pgvector adoption remain outside this bounded fixture implementation.

[Complete research bundles](research-complete-bundles.md) now add read-only export
and offline validation of these publications with their full selected ancestry.
The complete-history import lifecycle remains a subsequent implementation step.
