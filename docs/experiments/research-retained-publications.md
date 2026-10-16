# Retained fixture publications

`PublicationStore` retains an audited summary, analysis and dossier against one
immutable research revision. Reopening checks both the stored output bytes and the
complete revision/source reference chain. This is an isolated implementation slice
under [ADR-0069](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md) and the
approved exploration in [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).

**These are hand-authored fixture verdicts.** They exercise verification and
publication plumbing; they do not authenticate a verifier, establish semantic
truth, replace human calibration or authorize production research publication.
There is no public endpoint, provider call or default-stack change.

## Commit and reopen

A trusted caller supplies `PublicationContext`: the expected verification policy,
fixture verifier identity/version, renderer version and fixture auditor. The caller
must obtain this context independently of the submitted publication. Context is
canonicalized and bound into the reservation; payload-selected policy cannot
replace it.

`reserve_publication` requires the current retained revision, active root and
expected generation. It issues a publication UUID and charges the requested
capacity before rendering. Construction and initial fixture validation happen
outside write locks. The input envelope contains exactly `schema_version`
(`retained-fixture-publication/1`), `revision_id`, `research` and `publication`.
Existing fixture verification and render hashes remain unchanged; the outer
retained envelope uses schema-prefixed JCS hashing.

`commit_publication` checks all verification context, assessment and coverage rules
and requires passing render audits for exactly the summary, analysis and dossier.
The reserved publication UUID is the expected artifact-set identity. It derives
output bytes from the audited render inputs rather than accepting separate output
bytes. Under the existing scope-then-root locks it rechecks lifecycle, generation,
current revision, reservation and canonical revision/source closure. One transaction
stores the payload, all three outputs, derived source-reference ledger and receipt,
releases unused quota and sets root retention to thirty days from publication.

A competing revision advance makes an uncommitted publication stale. Rebuild with
a new reservation against the new revision; no silent rebase occurs. Successfully
published older sets remain readable while their root is active. An explicit historical re-render reservation is described below.

`read_publication` uses one repeatable-read transaction. It revalidates trusted
context, the pinned revision and every retained source; reconstructs each output;
and compares canonical payload, digest, exact output bytes and SQL/JSON source
references. A changed output or missing ledger entry fails closed. This protects
against detected corruption, not an arbitrary database owner who can rewrite both
data and trust expectations.

## Bounds, retention and deletion

The canonical payload plus all three UTF-8 outputs must total at most 1 MiB. This
counts duplicated embedded fixture structures and dossier bytes; nothing is
silently truncated. Existing root/scope logical quotas also apply. SQL metadata,
indexes and WAL are outside this logical accounting and still need measurement.

Unpublished roots retain the twenty-four-hour staging window. Once published,
source/revision/publication reservations and ordinary source/revision writes leave
the publication expiry unchanged; a new publication renews it to thirty days.
Reads, receipts, cancellations and replayed commits never renew retention. Expiry
blocks access; automatic garbage collection is still future work.

`publication_receipt` returns the committed input digest after an uncertain
acknowledgement, including after deletion. Same-input commit replay is idempotent
while the root remains active; changed input fails. Pending cancellation releases
its charge once. Reservation creation itself is not request-idempotent.

Root deletion removes publications and their source ledger before removing
revisions, snapshots and unshared blobs. It cancels pending publication operations,
releases charges and preserves only existing tombstone/receipt metadata. Copied
source text in outputs is purged along with original evidence. Late commits cannot
restore a deleted root. There is no restart-safe worker or authenticated operator
restore protocol implied by these operations.

## Explicit migration and validation

`migrate_publications` requires schema 2 and atomically applies
`003_fixture_publications.sql` under the schema-version lock. It refuses
reapplication. Initial installation still creates schema 1; source operations
support 1/2/3/4 and revision operations 2/3/4. Publication operations support 3/4;
historical re-render reservations require 4. Older
binaries reject unsupported schema versions. No automatic upgrade or destructive
downgrade is provided.

The `Back up schema two and exercise retained publications` step of
[Runtime CI](../../.github/workflows/runtime.yml) captures verified source and
revision manifests, dumps schema 2, runs eight actual publication database cases on
schema 3, restores the older dump into a new private database, and compares both
manifests. Cases cover exact reopen/replay/context, stale revision and failed audit,
lost commit acknowledgement, deletion/late commit, commit/delete race, expiry, output/ledger corruption, and
retention/reservation/migration refusal.

The subsequent [restore rehearsal](research-source-restore.md) includes published
retained and post-backup-deleted control roots. Every live restored publication
must reopen with all three exact outputs and pinned references; live receipts
must resolve, and deleted publication copies must be absent. Missing/incomplete
deletion history remains a rejection. These fixtures run in one isolated cluster,
not a cluster-loss or production RPO/RTO rehearsal. Reproduction requires the
explicit isolated Compose harness and new test databases; existing databases and
volumes are never dropped to force success.

Local admission tests exercise canonical representation and wrong policy, auditor,
revision, artifact set, failed/missing audit and changed output digest. Actual
PostgreSQL cases are required hosted CI, never replaced with mocks or skipped when
Docker is unavailable locally. Full Knowledge IR, authenticated verification,
export/import, aggregate admission, GC, capacity and remaining W2/W3 gates are still
incomplete. pgvector has not replaced Qdrant.

## Historical re-rendering

On schema 4, a caller may reserve a new publication with explicit `rerender_of`
(original publication UUID) and `original_context` keyword arguments. Both must
be supplied. The ordinary `revision` argument must match that original publication,
which may now be older than the current research revision. The new context may
select a different renderer or auditor, but must preserve verification policy and
verifier identity/version.

The reservation opens and validates the original publication under the root lock,
including its exact outputs and evidence closure. It records the original identity
and a schema-prefixed JCS digest of the **complete research envelope**. This pins
questions, assessments, verification records, conflicts, source dates and as-of
context, as well as structural evidence. Newly generated fixture audits cannot
silently legitimize changing any of those fields.

Commit still requires new passing audits bound to the newly issued artifact-set
UUID and trusted rendering context. For this explicit reservation it compares the
pinned research digest instead of requiring the current revision pointer. Ordinary
reservations retain the current-revision rule. The original publication and current
pointer stay unchanged. Exact same-input replay uses the existing receipt. Explicit
publication renews retention; reservations and reads do not renew an already
published root. Deleted or expired roots remain unavailable.

No acquisition callback, search client or provider is invoked by these storage
methods. A new presentation makes no new assertion of current factual freshness.
The caller constructs presentation inputs outside the transaction and supplies
fixture render audits; this does not introduce an automatic semantic renderer.

`migrate_rerenders` explicitly requires schema 3 and applies
`004_historical_rerender.sql` transactionally. Nullable operation metadata preserves
old reservation behavior and captures re-render provenance for new reservations.
Receipt metadata may retain the original UUID and research digest after deletion,
but no research text. Initial installation and old stored publication bytes/hashes
remain unchanged; older readers reject unsupported database versions.

Runtime CI dumps schema 3 and captures verified source/revision/publication
manifests before migration. It runs thirteen actual schema-4 cases (the eight
publication lifecycle regressions plus five historical-reuse cases), restores the
old dump into a fresh private database, and compares all three manifests. Historical
cases cover older-revision reuse with unchanged original/current pointer, changed
research despite recomputed audits, wrong original/context/policy, deletion before
commit, and migration refusal. The final schema-4 restore includes both original
and re-rendered retained/deleted controls and a fixed allowlist of trusted fixture
renderer contexts. It verifies copied presentations are purged after post-backup
deletion. No existing database or volume is removed to run these checks.

[Bounded artifact bundles](research-artifact-bundles.md) now support consistent
export and offline integrity validation with preserved original identities.
Recipient scope mapping and atomic import remain unimplemented.
