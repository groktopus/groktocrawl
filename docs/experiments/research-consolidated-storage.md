# Consolidated journey storage

This is the isolated, root-only storage integration for the
[consolidated fixture journey](research-consolidated-journey.md), under
[issue #94](https://github.com/magnus919/groktocrawl-x/issues/94) and accepted ADR-0075.
It adds schema 10 alongside existing formats; it is not production PostgreSQL
adoption, format freeze, workflow recovery or a vector-store decision.

## Transaction boundary

`ConsolidatedStore` shares the existing physical scopes, root quotas, connection
admission, staging, expiry and deletion paths. It creates a `consolidated` root and
reserves up to 5 MiB for two canonical documents and three report bodies, each still
limited to 1 MiB. Source reservations remain separate and occur before acquisition.
All reservations and committed bytes consume the existing 100 MiB root and 1 GiB
scope ceilings. Logical source references map explicitly to staged snapshot UUIDs
within the authorized physical scope/root; no URL grants source access.

A publication reservation pins the exact logical knowledge context. The journey's
optional server-owned commit callback runs while both execution owners remain live.
`commit_consolidated()` ignores a supplied candidate's assertion of trust: it reruns
the publication gate against exact staged sources and execution receipts. Under the
scope/root lock it rechecks generation, liveness, root format, the reserved context,
source bytes and an empty current revision. Knowledge, manifest, three reports,
source references, charges, operation receipt and retention transition commit
atomically. Both execution bindings are checked again before leaving the transaction.
No model, verifier or renderer callback runs under a database write lock.

This slice deliberately supports one root revision and one publication per root.
It rejects a competing publication after another commits. Successor revisions,
historical re-render and import/export for the new format remain unsupported; old
formats retain their existing behavior. A successful publication receives 30-day
retention, as in the existing experimental storage policy.

Pending reservations can be cancelled and refunded. A lost commit acknowledgement
must be reconciled through `consolidated_receipt()`; the journey does not retry it.
The receipt contains only the manifest digest, remains after deletion, and does not
make deleted content readable. Read-back revalidates canonical bytes, exact source
closure, manifest spans, publication eligibility and fixture provenance in one
repeatable-read transaction. It does not claim fresh executor activity after restart.

## Migration and deletion

Migration `010_consolidated_journey.sql` runs explicitly from schema 9 only; no
application startup installs it. Existing reader gates accept schema 10 without
rewriting legacy rows or canonical bytes. New tables hold consolidated publications,
source references and operation metadata. Existing purge/expiry processing removes
the new payloads and references, cancels pending operations, clears the current
pointer and releases charges before deleting shared source references. Committed
metadata receipts remain. Public authentication and production rollout are excluded.

The hosted probe takes a schema-9 backup before migration and restores it into a
separate scratch database, comparing existing reader outputs. Its schema-10 suite
includes existing lifecycle/history/publication/export/import regressions plus new
consolidated transaction cases. Rollback is the verified scratch restore of the
prior schema, not a destructive reverse migration over new retained data.

## Confirmation evidence

Local tests cover commit-hook lifetime and failure propagation along with the
existing journey and gate controls. Actual database validation runs in the hosted
`PostgreSQL Storage Probes` job:

- Exact knowledge/manifest/source/report round trip and explicit fixture provenance.
- Reservation cancellation, quota accounting, scope isolation and competing commits.
- Generation change, expiry and deletion between execution and commit.
- Closed execution owner, corrupted report, transaction rollback and lost acknowledgement.
- Scratch backup/restore with a separate current deletion inventory. Missing or stale
  deletion inventories fail before the restore is released. Reapplying deletion
  removes payload rows/references while preserving the receipt; retained reports
  must keep the same digest and fixture provenance.

The restore result is uploaded as `consolidated-storage-restore-result`. Successful
CI is required before claiming these PostgreSQL guarantees. The rehearsal uses only
fictional fixtures and a dedicated scratch database; it does not establish a
production recovery objective or a restart-safe research executor.

## Verified bounded milestone

[PR #101](https://github.com/magnus919/groktocrawl-x/pull/101) merged on
2026-09-06. All five workflows passed on merged commit
`ebf0d4baf7fd875959ebdd75bea20d21c75d9f12`, including the
[Runtime CI run](https://github.com/magnus919/groktocrawl-x/actions/runs/34064859486).
The PR's [database probe run](https://github.com/magnus919/groktocrawl-x/actions/runs/34063576277)
passed 85 database tests: 11 consolidated cases and 74 inherited regression cases.
Its downloaded `consolidated-storage-restore-result` artifact confirmed exact
retained content, deleted content unavailable, deleted payload rows and references
purged, metadata receipt preserved, and fixture provenance preserved.
The local unit/service suite passed 3,271 tests with seven skips.

This completes the implemented journey and isolated retained round trip requested
by #94. It demonstrates that the experimental components work together and reject
the tested invalid states. The judgments and reports still come from authored
fixtures. Real research quality, independent verifier accuracy, provider adapters,
consolidated successor history and operational deployment remain outside this
milestone; PostgreSQL vector storage and Qdrant replacement are still undecided.
