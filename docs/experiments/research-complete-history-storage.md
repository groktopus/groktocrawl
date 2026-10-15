# Complete fixture history storage

Second implementation step for [issue #71](https://github.com/magnus919/groktocrawl-x/issues/71),
using [complete revision admission](research-complete-revision-admission.md).
`ResearchStore` adds scoped PostgreSQL persistence for complete fixture revisions.
**Publication, re-render and bundle integration for these new roots remain open.**
This is not `knowledge-ir/1`, semantic verification or production storage adoption.

## Explicit root and schema boundary

Call `migrate_research()` explicitly on isolated schema 6 after a private backup.
Schema 7 adds complete revision operations, immutable payload rows, source reference
ledgers and a separate current pointer. Normal operations do not migrate.

Existing roots retain `revision_format='structure'`, their payloads, pointers,
receipts and readers. `create_research_root(scope, quota)` creates a new native root
with `revision_format='research'`. Source staging works for either native format;
structural revision writes reject complete roots, and complete writes reject legacy
or import-only roots. No old row is backfilled with invented objective, creation
time, verification or predecessor metadata. The prototype does not combine two
revision histories in one root or convert existing roots in place.

The caller is a trusted server responsible for scope authorization. This creates
no public endpoint or new principal/membership authority. Public delivery remains
later work under ADR-0072. Existing publication/export methods require legacy
structural publications and cannot publish or export these new complete roots yet.

## Reserve, commit, reopen and reconcile

1. `reserve_research(scope, root, generation, parent, size)` locks scope then root,
   checks native complete format, active lifecycle/generation and current parent,
   reserves quota and returns a server UUID. It renews valid staging activity.
2. `commit_research(scope, root, generation, revision, raw)` performs bounded local
   representation/model checks before write locks. It then locks the root, rechecks
   reservation/generation/current parent, loads the complete stored parent chain,
   validates canonical bytes/digests, typed history and each ancestor's source ledger,
   and validates the candidate against that retained prefix. No supplied history
   can substitute for the database's parent chain.
3. Commit atomically inserts canonical payload and source references, converts
   reservation to actual charge, writes the committed digest receipt and moves the
   complete current pointer. The root uses the existing staging retention semantics;
   this commit is not publication and does not establish thirty-day published life.
4. `read_research(scope, root, revision)` uses one consistent read transaction. It
   traverses the selected revision's retained ancestry, validates every envelope,
   parent, source and ledger, then validates the complete chain. It can reopen an
   older retained revision without treating it as the latest parent or fresh knowledge.
5. Identical committed input replays its identity without another charge or renewal;
   changed input conflicts. `research_receipt` returns the committed digest after a
   lost acknowledgement and even after payload deletion. `cancel_research` releases
   a pending reservation once; it does not reverse an already committed revision.

Canonical scope/root/revision/parent and snapshot UUIDs must match the retained
store's identities. Source closure checks exact UTF-8 body, digest, URL descriptor
and normalization. Dates, lineage and semantic labels remain fixture assertions.
Verification/assessment IDs, questions/conflicts and introductions now participate
in retained full-history validation, including removed/reintroduced entity IDs.

## Bounds and deletion

The existing 1 MiB canonical envelope bound, twenty-revision history limit,
root/scope logical quotas and transaction/SQL/lock timeouts apply. Each read may
materialize and validate the complete bounded chain; no aggregate concurrency or
measured memory/throughput claim follows. Reservation creation is not request-
idempotent; lost reservation responses and reservations on still-live roots require
explicit reconciliation/cancellation, not automatic job recovery.

Explicit deletion and expiry collection clear the complete pointer, delete all
complete revision payloads/ledgers, cancel pending complete operations and release
root charges before removing snapshots/unshared blobs. Minimal receipt/tombstone
metadata remains. Late commits and fresh reads fail after deletion. Previously
started consistent reads may finish. No database/volume deletion or immediate
physical file shrink is part of this behavior.

## Required CI evidence

The private `research` phase runs eleven complete-history database cases and all
twenty-nine expiry/import/export regressions on schema 7, following the prior
ninety-two storage cases. New cases cover complete root/successor reopening and
receipts, changed verification/assessment IDs, removed/reintroduced questions,
concurrent children of one parent, format separation, missing source/wrong scope,
size/quota/cancellation, expiry/deletion, corrupt source ledger, before-commit rollback,
lost commit acknowledgement and the twenty-revision limit. These are bounded tested
interleavings, not an exhaustive concurrency proof.

A private schema-6 dump is restored into a separate database and compared through
source/revision/publication/import manifests. Final schema-7 recovery includes two
complete-history controls: each has two revisions, one is expired/collected after
backup and the other remains retained. Missing complete-history deletion information
keeps the restore quarantined. Valid reconciliation denies the removed history,
validates all retained complete revisions and receipts, and reports
`verified_complete_research_revisions`. No payload dump is uploaded.

Actual hosted PostgreSQL and full runtime regressions must pass before merge;
local fixture construction and pure admission tests are not database evidence.
Future publication/interchange integration must pin this complete identity and
carry its required history explicitly before issue #71 can close.
