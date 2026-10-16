# Consolidated research format contract

**Accepted for bounded experimental implementation; not implemented or frozen.**
Magnus accepted the proposal on 2026-09-06; see
[acceptance record #92](https://github.com/magnus919/groktocrawl-x/issues/92). Companion to
[ADR-0075](../adr/experimental/groktocrawl-x/0075-consolidate-research-interchange-contracts.md), issue
[#90](https://github.com/magnus919/groktocrawl-x/issues/90). Experimental fork only.
The [existing compatibility inventory](../../tests/contracts/research/README.md)
remains authoritative for implemented prototype readers.

The intended benefit is one reusable knowledge revision, with separately audited
presentations. A new rendering does not mutate claims or imply fresher evidence.

## Common representation rules

Objects accept exactly their declared fields. Every field is present; optional
values are explicit nulls, collections may be empty where stated. No coercion or
implicit defaults. IDs are nonempty opaque strings bounded to 200 characters,
unique within the research root across entity kinds; scope/research/revision IDs
remain explicit. UTC timestamps use `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, zero through six
fractional digits. Hashes are lowercase 64-character SHA-256 hex. Integers are JSON
integers in the safe-integer range with field-specific nonnegative bounds. Numbers
that require exact decimal precision use separately specified strings; none are
introduced here. Unknown version, key, duplicate key, invalid Unicode, non-finite
number or unresolved reference is an admission error.

Use the existing JCS implementation and reject input before filling any missing
fields. SHA-256 hashes the format's UTF-8 version string, one zero byte, then its
canonical bytes; the digest lives outside the hashed object. Original source/output
byte hashes have no JSON-domain prefix. Canonical envelopes and encoded bundles
remain at most 1 MiB, histories at most 20 revisions, source bodies at most 10 MiB,
root quota 100 MiB and scope quota 1 GiB. Per-entity text/count limits start from the
current typed fixture limits and must be enumerated in the implementation schema;
no bound may silently increase during consolidation.

## Knowledge IR envelope

The exact top-level fields are:

| Field | Representation and rule |
|---|---|
| `schema_version` | Literal `knowledge-ir/1` |
| `scope_id`, `research_id`, `revision_id` | Explicit IDs checked against independently supplied caller context |
| `parent_revision_id`, `parent_digest` | Both null for root; otherwise exact prior revision ID and IR digest |
| `created_at` | UTC creation time, no earlier than its recorded inputs or parent |
| `objective` | Required nonempty bounded text |
| `as_of` | UTC time or null; no implied currentness when unknown |
| `policy_version` | Required research-policy identity |
| `snapshots`, `evidence`, `claims`, `relationships` | Entity arrays described below |
| `questions`, `conflicts` | Required question/outcome and conflict records |
| `verification_inputs`, `verifications`, `assessments`, `assessment_links` | Explicit check inputs, results and claim assessment mapping |
| `introductions` | Newly introduced entity kind/ID and nullable predecessor ID |
| `coverage` | `complete`, `partial` or `insufficient`, recomputed from required question outcomes |

Collections use declared order as part of byte identity; do not silently reorder
arrays. Object key order is governed by JCS. Inputs and results use IDs distinct
from entity/revision IDs. Every referenced ID resolves within the revision or its
explicit validated history where predecessor references require it.

### Sources and evidence

A snapshot has `snapshot_id`, `canonical_url`, `retrieved_at`,
`normalization_version`, `media_type`, `content_ref`, `content_digest`,
`content_bytes`, `published_at`, `effective_at`, `origin_id`, and `lineage_id`.
Dates are null or `{value, provenance}`. Origin/lineage are nullable IDs describing
recorded source dependence, not references that require fetching another root.
Supported media types initially remain text/plain and text/markdown.

`content_ref` is `{scope_id, research_id, snapshot_id}` in the retained authority's
logical namespace; it is not a fetchable URL or credential. Its snapshot ID matches
the descriptor. Import preserves original logical identity through the existing
recipient mapping and deletion authority. The trusted resolver checks caller
access, root generation/liveness, exact byte length/digest and normalization/media
metadata. It never dereferences an arbitrary URL from an IR. Missing/deleted data
returns unavailable evidence; no substitution or automatic retrieval.

Standalone JSON is not a self-contained evidence archive. Offline validation needs
a bounded bundle providing exact source bytes and full required ancestry; online
admission needs the authorized resolver. The 1 MiB encoded bundle bound still
applies even when individual retained sources can be larger.

Evidence preserves `{evidence_id, snapshot_id, start, end, quote, quote_digest}`.
Offsets are zero-based half-open Unicode code points into the exact decoded source.
Validate UTF-8, bounds, quote equality and digest against resolved bytes. Unknown
binary/PDF locators are rejected. Source URL and quote equality do not prove support.

### Claims, relationships, questions and history

A claim has `{claim_id, text, kind, qualifiers, temporal_scope}`. Kind and temporal
scope retain existing enums. Assessment outcomes are kept outside claim content in
`assessment_links`: `{claim_id, state, assessment_ids}` for every claim, including
`unassessed` with an empty list. Other states require matching assessment records;
they never substitute for verification. This separates a stable proposition from
new judgments about it. Changes to text/kind/qualifiers/temporal scope require a new
claim ID and predecessor declaration; a new assessment requires its own new ID.

Relationships preserve existing kind, source/target IDs, rationale, nullable rule
and assumptions. Support/contradiction goes evidence → claim; derivation goes
claim → premise. Reject dangling references and derivation cycles. No automatic
transitive support. Questions retain ID, text, answered/unresolved status and a
reporting claim. Conflict records use `{conflict_id, question_id, claim_ids,
evidence_ids, reason}` with at least one claim and two distinct evidence IDs; all
claims/evidence resolve and the question is unresolved. Multiple incompatible claims
can now belong to one group. Contradiction edges require an explicit matching group.

At least one required question is present. Coverage is complete only when all are
answered, insufficient when none are, otherwise partial. Outcomes remain recorded
judgments; semantic evaluation must establish whether they are warranted.

Validate the entire bounded retained prefix for immutable entity identities,
introductions/predecessors and chronology, including removed/reintroduced entities.
New check results never overwrite old ones. Branching/merge histories are excluded
from v1. The new assessment mapping is a scoped refinement of the fixture layout;
old claim objects and their hashes are not rewritten.

## Verification input and reviewer contract

Each input has `{schema_version, input_id, check_type, subject_id, policy_version,
reviewer, context, evidence_ids, freshness}`. Version is `knowledge-check-input/1`; check types
are structural, semantic_support, freshness, conflict_coverage and assessment.
`context` is the exact projection of the enclosing IR's `scope_id`, `research_id`,
`revision_id`, `parent_revision_id`, `parent_digest`, `created_at`, `objective`,
`as_of`, `policy_version`, `snapshots`, `evidence`, `claims`, `relationships`,
`questions`, and `conflicts`. It excludes every input/result collection, assessment
mapping, introductions, coverage and the enclosing IR digest. Initially bind this
full projection for every check type; do not let the producer select a favorable
subset. The resolver supplies all referenced source bytes for context inspection.
Large repeated projections can hit the 1 MiB limit; reject explicitly rather than
prune context. Future shared input storage needs its own measured format revision.

The input also declares `freshness`: null except for freshness checks, otherwise
`{evaluated_at, sources}`. Each source basis records snapshot ID, basis
(published_at/effective_at/historical_snapshot/unknown), maximum age in seconds and
bounded rationale, preserving the existing fixture meaning. The source set must
match the subject claim's support/contradiction and transitive premise closure;
all descriptors also remain in the full context. `evidence_ids` must name that
closure for support/freshness/assessment checks, and all evidence for structural
and conflict/coverage checks, without duplicates. Structural and conflict/coverage
subjects are the revision ID; other subjects must be a local claim. Arrays follow
the corresponding IR order. These rules define deterministic input binding without
making evidence selection an unrecorded model choice.

`reviewer` is a discriminated record. All kinds require identity and version:

- `fixture`: fixture expectation identity; never treated as independent assessment.
- `tool`: implementation/version and configuration digest.
- `model`: provider, model identity, prompt digest and generation-configuration
  digest. Record requested model separately from nullable resolved model identity;
  do not claim a provider alias pins immutable weights.
- `human`: external attestation reference verified by the trusted review authority.
  A payload cannot grant itself human-reviewed status. Human record admission is
  disabled until that authority integration exists.

A verification has ID, input ID/digest, verdict (pass/fail/indeterminate), checked
UTC time and bounded reason. An assessment uses the same input binding but a
supported/contested/insufficient/refuted outcome. Both are immutable and append-only.
Recompute every input projection from the pinned revision/resolved sources; verify
its separate domain hash, subject, reviewer and policy. Metadata alone does not
prove a model/tool/human actually performed the check. The server must bind results
to the configured executor or verified attestation before publication. Fixture
checks permit only explicitly labeled fixture publications, never a promotion to
independently verified research.

Deterministic admission separately validates the complete envelope, introductions
and retained history; a structural check result over the non-result projection
cannot waive those checks.

No input includes its own result or full IR digest, preventing a hash cycle.
Changes to the checked context or reviewer require a new input and result ID.
All positive publication eligibility rules from ADR-0069 remain; a recorded pass
from an untrusted submitter is insufficient.

## Separate render manifest

Exact fields: `schema_version` (`render-manifest/1`), `scope_id`, `research_id`,
`artifact_set_id`, `revision_id`, `revision_digest`, `created_at`, `renderer`,
`coverage`, `artifacts`, `audit_inputs`, `audits`.

Renderer is `{identity, version, configuration_digest}`. Artifacts contain exactly
one summary, analysis and dossier; each has ID, layer, immutable content reference,
byte digest/length, material statement mappings, question IDs and conflict IDs.
Mappings record rendered code-point start/end, exact statement text, claim IDs and
evidence IDs. Reopen actual output bytes and validate spans, references and coverage.
The auditor must also assess omitted material assertions/caveats; mappings alone
cannot prove every material assertion has been included.

Define `render-audit-input/1` with input ID, reviewer and `manifest_core`, the exact
manifest fields above excluding `audit_inputs` and `audits`. All three output
descriptors/mappings are in that core. Audit records bind input ID/digest, verdict,
checked time and reason. Require passing structural checks and an authorized
applicable render audit before publication. This avoids an audit hashing the
manifest containing that same audit. The final manifest digest includes both audit
inputs and results. Audit inputs cannot mutate the core they claim to inspect.

Ordinary publication pins the current validated IR; explicit historical re-render
pins the retained original research/policy context. New wording gets new artifacts,
manifest and audits. Freshness claims requiring new evidence create a new IR
revision. Publication and retained references still commit atomically under the
storage lifecycle contract; this design does not add public authentication or
restart-safe execution.

## Compatibility and delivery

Keep every old reader, discriminator, receipt and digest rule. New readers reject
old payloads presented under new names. No in-place conversion or automatic model
reserialization. Any explicit future conversion returns a new artifact with recorded
origin, keeps the original bytes, checks complete history/source closure, and reports
missing requirements rather than synthesizing provenance. Old fixture verdicts can
only remain fixture provenance; they cannot become model or human review.

Implement in two coherent stages after contract review: first typed consolidated
admission, source resolution and golden positive/negative examples including the
check-specific projections; then one bounded acquisition-to-three-layer-output
journey and isolated retained round trip. Freeze format names only after those
contract tests and the field/version review pass. Until then use visibly proposed
schema artifacts, with no production writer or implicit migration. Independent
semantic evaluation follows separately and requires its own budget/reviewer gates.

## Implementation progress

[Strict knowledge context admission](research-knowledge-context.md) implements the
non-result context and caller-supplied source-resolution boundary under issue #94.
It uses an explicit prototype envelope and preserves legacy formats. Full check
records, history, manifest admission, the research journey and retained integration
remain incomplete; this is not format freeze.

[Input-bound checks and supplied history](research-checked-knowledge.md) now extend
that context with immutable verification/assessment records and full prefix checks.
They remain prototype contracts, without publication authority or stored-current-parent
proof. Configured knowledge execution is now implemented; publication integration, the
journey and retained integration remain under #94. See the
[manifest admission guide](research-render-manifest.md) for pinned output validation
and its remaining audit execution/publication boundary.

The [bounded consolidated fixture journey](research-consolidated-journey.md) now
connects acquisition, construction, executed verification, all three report layers
and the publication candidate gate. It preserves fixture provenance and incomplete
coverage. Actual retained transaction/round-trip integration remains under #94.
