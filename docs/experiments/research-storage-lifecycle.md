# Research storage lifecycle and failure matrix

Design scenarios for exploration-approved [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md),
using the [conflicting-price example](research-foundation-example.md). These are
acceptance scenarios to implement in W3, not evidence of executed database tests.

## Publication and reuse journey

Use scope `fixture-scope`, research `research-price-001`, snapshots `src-pricing`
and `src-help`, IR `ir-001` and output set `render-001`. Start at
`2026-09-01T12:00:00Z`; use the exact source bytes and hashes from the foundation
example. All sources and timestamps are synthetic.

| Step | Operation | Expected authoritative state | Observable result |
|---|---|---|---|
| 1 | Admit a root and reserve bounded acquisition/storage bytes | Active root, generation 1, quota reservation; no published IR/set | Work admitted, no answer yet |
| 2 | Stage both snapshots with operation IDs | Scoped blobs and immutable descriptors committed; staging references protect them | Sources can be inspected within the authorized run |
| 3 | Commit ir-001 with parent null and matching generation | IR bytes, full reference ledger, receipt and head pointer commit together | IR available for verification/rendering, not a final answer |
| 4 | Publish render-001 | Summary, analysis, dossier and audit mapping all pin ir-001; one manifest/receipt | One qualified final answer, price conflict retained |
| 5 | Expire the short-lived session | Session keys disappear; research root/retained closure unchanged | Reopen retained research by artifact identity after authentication |
| 6 | Re-render ir-001 with a new renderer version | New render-set identity pins identical IR and source bytes; explicit publication may renew root retention | New presentation; no search, scrape or new factual freshness claim |
| 7 | Export and import into a fresh authorized scope | Verified bundle, scope mapping and preserved immutable IR bytes; no runnable job | Historical artifact remains auditable without provider calls |
| 8 | Reach root retention deadline with no extension | Root unavailable to normal reads, then dependency-aware purge | Explicit expiry; never a fresh fetch under an old snapshot ID |

Ordinary reads and session activity do not renew retention. A later as-of request
requires freshness assessment and potentially new source snapshots; the newly
rendered date is not the age of the underlying evidence.

## Failure and interleaving matrix

Each row is a required real-database acceptance scenario. Fault injection must
name the exact transaction boundary and show pre/post state plus sanitized receipts.

| Case | Injection / interleaving | Required outcome |
|---|---|---|
| Duplicate source write | Same operation ID and input digest twice | Return one committed identity; no duplicate accounting/effect |
| Conflicting retry | Same operation ID, different bytes/digest | Explicit conflict; existing immutable bytes unchanged |
| Unknown commit result | Drop connection after COMMIT before ACK | Receipt resolves whether effect committed; retry does not reacquire sources |
| Concurrent IR heads | Two writers use the same expected parent | One advances head; other gets stale-parent outcome and recomputes outside transaction |
| Crash before IR commit | Snapshot staging exists, revision transaction aborts | No published IR/manifest; staging protected until its deadline, then collectible |
| Crash before render publication | One or more render blobs staged | No partial final manifest; a retry resolves receipt/staging before publication |
| Cancel during commit | Cancellation arrives after storage transaction starts | Reconcile receipt; do not claim committed bytes rolled back; D5/D6 decide terminal job/event precedence |
| Delete vs late writer | Tombstone/generation advances before writer commit | Writer rejected; no recreation, no final artifact notification |
| Tombstone already purged | Old operation targets now-missing root | Reject write; only server-created new identities can start new roots |
| GC vs new publication | Collector and writer compete for scope/root locks | Atomic reference recheck; no published reference to collected evidence |
| Quota race | Concurrent reservations approach scope/root limit | Committed plus reserved logical bytes never exceed admitted ceiling |
| Oversized input | Snapshot exceeds 10 MiB proposed cap | Explicit rejection/partial acquisition; no hidden truncation of evidence |
| Changed bytes | Persist/read body disagrees with declared digest | Integrity failure; block IR/publication/read as appropriate |
| Unicode/JCS disagreement | Supplementary keys, escapes, unsafe numbers, duplicate keys | Conformant deterministic bytes or explicit rejection; no accidental default JSON serialization |
| Cross-scope reference | IR references a blob from another authorization scope | Authorization/composite-reference failure; no existence/content disclosure |
| Stale search result after deletion | Qdrant still returns deleted research ID | Authoritative lifecycle check rejects resolution; no deleted evidence returned |
| Targeted source purge | Remove a source also quoted by several artifacts | Dependent IR/outputs invalidated/purged; copied quote text does not survive as accessible evidence |
| Interrupted import | Kill importer before final bundle publication | No exposed partial bundle; bounded staging reclaimed |
| Unsupported schema | Import bundle needs an unavailable schema reader | Fail before publication; retain no falsely usable manifest |
| Changed export file | Alter one byte of a source or IR member | Import rejects the bundle; no fetching live URLs to repair it |
| Backup/restore | Restore bounded corpus into clean isolated database | Digests, canonical bytes, scopes, manifests, receipts and reference closure verified before opening access |
| Restore before a later deletion | Backup includes subsequently deleted root | Quarantined until deletion inventory reconciled; missing deletion history blocks re-exposure |
| Lost derived index | Delete Selected vector/cache projections | Retained evidence is readable from authority; rebuild indexes only from authorized retained roots |

## Vector consolidation acceptance scenarios

Run these only if PostgreSQL is adopted, following ADR-0071's fixed comparison
manifest. All are unexecuted; passing byte-storage tests alone does not select a
vector backend. Compare PostgreSQL + pgvector with PostgreSQL + Qdrant.

| Scenario | Required evidence |
|---|---|
| Identical vectors and queries | Exact cosine parity within declared tolerance; ANN recall@k against exact eligible results; downstream ranking regression checks |
| Scope, freshness and deleted-root filters | No unauthorized/stale-deleted evidence exposed; recall and result count measured across declared filter selectivities |
| Active-model migration | Backfill and switch between declared model versions/dimensions; no mixed embedding spaces; rollback preserves query behavior |
| Batch upsert/delete and retention | Stable identity, metadata/stats parity and bounded retries; vector eviction cannot erase retained evidence |
| Search plus ingestion plus research publication | p50/p95 and throughput meet predeclared limits at representative scale, including default 250,000-document capacity; no publication starvation |
| Index maintenance, timeout and failure | Bounded connections/queues, explicit errors and research authority isolation; record total RAM/CPU/disk/WAL footprint |
| Backup, restore and cutover | Verify retained byte hashes, model/version inventory and query parity; measure restore/rebuild; rehearse rollback before removing Qdrant configuration |

Record the consolidation decision and any unmet requirement. Passing gates supports
removing Qdrant from the experimental deployment; retaining both needs an explicit
reason. Old-volume deletion requires separate authorization. Embedding/reranking
remain semantic-svc responsibilities unless another reviewed decision changes them.

## Evidence and next implementation work

The W3 implementation issue must link ADR acceptance, schema/migrations, real-DB
test invocation and fixture versions, scope and quota concurrency load, export
format/version, backup and restore procedure, and the sanitized results of each
case. Explicitly mark any omitted failure mode unverified. All publication and
integrity cases are hard gates; timing/footprint thresholds come from the reviewed
ADR-0070 baseline manifest before comparative runs.

The isolated implementation now uses PostgreSQL and psycopg, with source, revision,
publication, rerender, export and same-authority import cases in required CI.
The [readiness checkpoint](research-w2-readiness.md) distinguishes those executed
fixture contracts from this full acceptance matrix. [Expiry collection](research-expiry-collection.md)
is implemented as a bounded internal call, with actual database race/restore cases
required; automated scheduling remains absent. D5 must still define
workflow ownership, crash resumption, staging renewal, retries and webhook outbox;
D6 must define public errors, references, session transitions and CLI/API parity.
A storage test passing cannot be reported as end-to-end durable job completion.

The [capacity probe design](research-storage-capacity.md) predeclares source-heavy
root workloads, concurrent small publications, explicit quota/format boundaries
and backup measurements. The [executed findings](research-storage-capacity-findings.md)
record bounded feasibility evidence; they do not establish a capacity SLO or full
W3 acceptance.
