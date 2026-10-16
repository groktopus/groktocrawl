# Complete fixture revision admission

First implementation step for [issue #71](https://github.com/magnus919/groktocrawl-x/issues/71),
following the [compatibility disposition](research-ir-compatibility.md).
`admit_research_revision` validates a complete supplied fixture revision and its
supplied predecessor chain. **It does not persist revisions or establish trusted
history, current parent ownership, source-store availability or semantic truth.**

## Envelope and expected identity

The canonical envelope has exactly two keys: `schema_version` is
`retained-research-revision-prototype/1`, and `revision` is a complete nested
`fixture-revision/1` object.

The actual `revision` is the existing `FixtureRevision` object with its explicit
parent, creation time, complete research and introductions. Its validators require
an objective, check chronology, and validate structure, assessments, verifications,
questions and conflicts. No missing field is synthesized from current time or a
legacy structural database row.

Callers provide independent expected `scope_id`, `research_id`, `revision_id` and
nullable `parent_revision_id`, plus a tuple of prior raw complete envelopes ordered
from the root to the direct parent. IDs follow the existing opaque fixture identity
contract; this pure boundary does not grant access or make every fixture ID a
storage-admissible UUID. A future store must bind its own identity/source rules.

Each envelope passes existing strict canonical JSON admission before typed model
validation. The candidate must match all expected identities. Every supplied prior
envelope is decoded and validated again, and `FixtureHistory` checks the complete
chain: same scope/research, root-first/direct-parent order, chronology, unique IDs,
new entity introductions, same-kind predecessors, and immutable records across
removal/reintroduction. A child without its full prefix is rejected. The caller must
obtain that prefix from trusted retained storage; arbitrary supplied bytes cannot
prove that the latest committed parent or actual history was used.

## Bytes, bounds and compatibility

The result is an immutable `AdmittedResearchRevision` containing canonical document
bytes/digest and the validated `FixtureRevision`. Canonicalization is the existing
schema-prefixed JCS rule. Verification and render input hashes retain their original
prototype algorithms; this wrapper does not rewrite or reinterpret them.

Each raw/canonical envelope is limited to 1 MiB with the existing nesting, node,
integer and Unicode rules. At most nineteen prior envelopes plus one candidate
are accepted. This bounds input to at most twenty 1 MiB envelopes; it is not a
measured in-memory footprint or aggregate-concurrency admission policy. The caller
cannot pass a generator that bypasses the prefix count. No database, provider,
file resolution, network source fetch or public endpoint is invoked.

Existing structural, publication, bundle and fixture history discriminators stay
unchanged. Unknown/extra envelope fields and versions fail. The implementation is
not `knowledge-ir/1`, a migration, or a compatibility upgrade for old stored rows.

## Evidence and remaining work

Unit tests cover exact round trip and preserved inner hashes, expected identity
mismatches, missing objective/time/introduction context, unknown fields/version,
missing parents, modified verification ID reuse, tampered prefix validation,
duplicate JSON, oversize input, and the inclusive twenty-revision bound. Existing
history tests cover the deeper structural/entity predecessor invariants.

[Complete history storage](research-complete-history-storage.md) now adds the
dependent persistence/source-closure/current-parent/receipt boundary on explicit
schema 7, with actual PostgreSQL migration/restore CI required. Publication, historical
re-render and export/import must then explicitly reference and carry complete
history before issue #71's end-to-end scope can be called complete. No new database
lifecycle or independent semantic evaluation evidence follows from these pure tests.
