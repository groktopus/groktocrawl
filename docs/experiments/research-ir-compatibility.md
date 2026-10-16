# Knowledge IR compatibility disposition

Audit of implemented prototype formats at `db3040da274816b035b0093a1d49f710585a97c1`,
after storage, import and expiry collection landed. Tracked in
[issue #71](https://github.com/magnus919/groktocrawl-x/issues/71).
[ADR-0069](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md) is the accepted
bounded foundation contract; its body is unchanged. This document does not freeze
`knowledge-ir/1`, change an accepted ADR or report W2/W3 complete.

## Verified completion of issue #71

The bounded complete-history implementation is complete as of main commit
`baf5f783eddbb77360fe242cd3fc7df36eef2149` (PR #78). A retained complete
research revision can now be reopened before rendering, published, historically
re-rendered, exported with its required ancestry, and imported within the same
live storage authority. Legacy structural formats retain their original readers,
bytes and digest interpretations.

| Declared requirement | Delivered implementation |
|---|---|
| Explicit compatibility disposition without invented legacy provenance | This original audit, PR #72; separate complete envelope admission, PR #73 |
| Retained complete parents, immutable identities and scoped source closure | Schema 7 and complete history storage, PR #74 |
| Publication and historical re-render bound to the complete revision | Complete publication admission and schema 8, PRs #75–#76 |
| Complete ancestry through versioned export/import | Complete bundles and schema 9 format-bound imports, PRs #77–#78 |
| Atomic receipts, lifecycle closure and migration/restore evidence | Actual PostgreSQL lifecycle and recovery checks through PR #78 |

[PR #78 Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/34028115924)
passed all 263 scheduled PostgreSQL cases, full integration and the runtime gate.
The schema-8 backup's six manifests matched its independent restore. The final
PostgreSQL 17.11 recovery report verified 125 complete research revisions,
35 complete publications, 35 complete exports and 11 complete imports alongside
legacy controls. It reconciled 334 deletion inventory entries; post-backup deletion
remained denied, expiry collection reconciled and live receipt references resolved.
These are test executions and retained fixture objects, not independent workloads
or measurements of semantic quality. The local unit/service suite passed 3,084
cases with seven existing skips.

All five workflows on that exact merged commit passed, including
[post-merge Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/34029205027).
This satisfies issue #71's declared bounded implementation and evidence scope.
The sections below preserve the original audit and intermediate milestones; their
“next” and “open” statements describe those earlier checkpoints.

W2/W3 are not thereby accepted as complete. Consolidated `knowledge-ir/1`, public
compatibility and authorization, and independent semantic evaluation remain separate
gates. Subsequent [connection admission](research-storage-admission.md) and
[capacity exploration](research-storage-capacity-findings.md) are complete within
their bounded scope. The current [field/version review](research-ir-contract-review.md)
defines the next consolidated-contract work. No production/default-stack
cutover, vector adoption, provider spend or restart-safe execution is established.

## Original audit conclusion and implementation plan

The prototype has substantial structural and storage validation, but **a retained
structural revision is not a complete research revision**. The complete in-memory
history model and persisted history are different contracts. Persisting a publication
preserves its research context, but does not apply the full history validator to a
chain of complete research revisions.

The next implementation should retain the existing complete fixture-revision shape
and validate successors against retained complete parents before rendering. Keep a
new explicit prototype storage discriminator and old readers. Do not rename the
current structure, publication or bundle to `knowledge-ir/1`, invent absent metadata
for old records, or change old verification hashes in place.

## Contract disposition

Paths below are under `agent-svc/agent/experimental/`. “Implemented” describes the
stated bounded fixture behavior, not independent semantic correctness or public
security. The next column is required work or an explicit retained limitation.

| ADR-0069 contract | Current implementation and boundary | Disposition |
|---|---|---|
| One versioned immutable revision with research/revision IDs and parent | `KnowledgeStructure` has schema/scope/research/revision IDs. `retained-structure-prototype/1` adds parent and canonical storage. `FixtureRevision` separately wraps complete research, parent, creation time and introductions. | Persist complete research revisions; retain structural readers as legacy, not silently complete v1 records. |
| Creation time, objective, questions/as-of, policy, coverage and unresolved questions | `FixtureRevision.created_at` enforces input chronology and non-null objective. `FixtureResearch.objective` alone is optional. Questions/conflicts and policy live under research/verifications; coverage is derived. The retained structural payload excludes those fields and creation time. | Store and validate the complete envelope. Distinguish canonical creation time from operational database timestamps. Specify explicit versus derived fields in the future interchange schema. |
| Immutable snapshot descriptor and resolvable content | `Snapshot` records URL, retrieval time, digest, normalization, text media, dates/provenance and lineage/origin. It embeds text. SourceStore separately retains exact bodies; RevisionStore validates scoped source closure. | Existing text path works. A future external content-reference representation needs a versioned resolver contract and migration, not a field rename or URL fetch during admission. |
| Exact text evidence locators | `Evidence` stores snapshot ID, code-point start/end, exact quote and UTF-8 digest; structure validation checks resolution and equality. | Preserve current text semantics. Binary/PDF locators remain intentionally inadmissible until separately specified. |
| Claim kinds, qualifiers and evidence assessment | `Claim` has all three kinds, qualifiers, temporal scope and five assessment states. `AssessmentLink` maps claims to explicit assessment records; verification inputs identify subjects. | Freeze how links appear in the consolidated schema. Do not call field-name differences a semantic failure where an explicit mapping already exists. Observation labels remain caller assertions, not authenticated measurement provenance. |
| Directed support/conflict/derivation with scoped rationale | `Relationship` has endpoint IDs, rationale, rule and assumptions; structural validation enforces direction, references and acyclic derivation. `Conflict` links one report claim/question to evidence. | Preserve deterministic checks. Document whether the narrower report-claim conflict representation fully expresses the chosen v1 cases; do not infer source independence or transitive entailment. |
| Verification subject, kind, verdict, input digest, time, reason and evidence | `FixtureVerification` and `VerificationInput` provide those fields with policy and fixture verifier identity/version. Digests bind the complete structural input. | Fixture-only verifier kind is intentional. Model/prompt provenance and authenticated machine/human identities need their own admitted representations before non-fixture use. |
| Stable IDs, predecessors and append-only verification across revisions | `FixtureHistory` checks introductions, predecessors, cross-kind IDs and immutable records across complete supplied revisions. Retained `RevisionStore.entity_records` compares snapshots/evidence/claims/relationships only. | **Primary gap:** apply complete history validation against retained complete parents, including assessments, verification, questions and conflicts. A publication's local validation does not establish this historical invariant. |
| Three rendered layers, IDs/digests and statement mappings | `RenderInput`, `FixtureArtifact`, `FixtureRenderAudit` and `FixturePublication` bind artifact set, renderer/auditor, statement/claim/evidence maps and three outputs. PublicationStore persists canonical publication plus exact output bytes and reference ledger. | Preserve the fixture manifest and audit checks. A future public manifest must pin the complete research revision identity, not silently equate structural and full research revisions. |
| Whole-envelope canonicalization | `canonical.py` supplies bounded JCS and schema-prefixed SHA-256 for retained envelopes. Verification/render input hashes use their own documented prototype serialization. | Preserve each legacy hash algorithm under its existing discriminator. JCS on an outer envelope does not retroactively make inner prototype hashes JCS. Any new algorithm gets an explicit version and golden compatibility cases. |
| Retain, re-render, export/import, deletion and expiry | Schema 6 stores sources, structural revisions and publication context; historical re-render pins original research bytes. Bundles include selected publication, structural ancestry and source closure. Imports preserve original identities with a separate recipient mapping. Expiry/deletion purges dependent copies. | These lifecycle paths are implemented and tested. Bundles do not contain complete predecessor declarations and complete research context for every structural ancestor. Extend only after complete revision persistence; keep old bundle readers and bounds. |
| Independent support/freshness/conflict/render judgment | Fixtures bind supplied verdicts and require publication eligibility; source dates/lineage and semantic outcomes are authored assertions. | Remains a separate evaluation gate. No stored hash, passing database test or fixture verifier record establishes truth, human review or calibrated judge performance. |

Source entry points: [knowledge.py](../../agent-svc/agent/experimental/knowledge.py),
[verification.py](../../agent-svc/agent/experimental/verification.py),
[publication.py](../../agent-svc/agent/experimental/publication.py),
[revisions.py](../../agent-svc/agent/experimental/revisions.py),
[revision_store.py](../../agent-svc/agent/experimental/revision_store.py),
[publication_store.py](../../agent-svc/agent/experimental/publication_store.py), and
[artifact_bundle.py](../../agent-svc/agent/experimental/artifact_bundle.py).

## Implementation order for issue #71

1. Define canonical admission of a complete fixture revision using existing typed
   validation, caller-selected scope/research/revision and an explicit parent.
   Keep the 1 MiB admission bound unless a separate measured change is justified.
   Reject missing objective/time/introduction context; do not fill it from ambient
   time, a publication or a structural revision's database row.
2. Add explicit persistence and migration with atomic receipt/current-parent/source
   closure. Validate the bounded complete retained chain before commit and reopen.
   Exercise changed/reused verification and assessment IDs, removed/reintroduced
   entities, stale parents, cross-scope references and cancellation/ambiguous commit.
3. Integrate publication and historical re-render with the complete pinned revision;
   extend export/import to include its required history with explicit format versions.
   Old stored bytes and receipt identities remain readable through old contracts.
4. Rehearse migration/restore and deletion/import/expiry closure on actual PostgreSQL.
   Update this disposition with evidence before considering a consolidated schema
   freeze or external client exposure.

These are dependent implementation steps, not authorization to widen provider,
production, public authentication, runtime or recovery scope. Split reviewable PRs
at complete transaction/format boundaries; do not call step 1 complete end-to-end
compatibility. A new database schema version alone does not version every payload.

## Compatibility and completion gates

- Keep all existing prototype discriminators and hash interpretations stable.
  Existing files/rows must not acquire missing semantic provenance during migration.
- Use explicit new envelope versions and reject unknown versions before publication;
  provide positive and negative golden examples and exact round-trip expectations.
- Preserve current size/depth/count limits or separately document and measure any
  changed bounds. ADR-0071's proposed 5 MiB IR ceiling is not the implemented 1 MiB
  canonical admission limit and must not be reported as supported capacity.
- Preserve immutable source bytes, source/ref closure, original scope identities,
  current deletion authority and complete predecessor requirements through the
  publication/export/import path. Public principal authorization remains W6 work.
- Keep independent semantic evaluation, human calibration and model/prompt provenance
  outside the claim that a structural/persistence test passed. Human fields remain
  unset; Hermes is an AI reviewer, not a human-label substitute.

The storage lifecycle's 92 real PostgreSQL cases and restore evidence are recorded
in [issue #1](https://github.com/magnus919/groktocrawl-x/issues/1#issuecomment-5557113293).
All five workflows for the audit base commit passed. This audit adds no executed
compatibility feature or additional database evidence.

## First implementation step

[Complete fixture revision admission](research-complete-revision-admission.md) now
canonicalizes the explicit full envelope and validates a bounded supplied prefix
against caller identities. This implements step 1's pure admission boundary. It
does not persist or authenticate that prefix, resolve sources from storage, or
establish the latest parent. Steps 2–4 remain open under issue #71.

## Retained complete-history step

[Complete fixture history storage](research-complete-history-storage.md) adds explicit
schema-7 persistence with a separate root format, complete retained-parent validation,
source ledgers and stable receipts. Legacy structural roots remain unchanged. This
implements the persistence boundary in step 2, subject to required actual database
and restore CI. Publication/rerender and export/import integration in steps 3–4
remain open; those operations do not yet consume complete-history roots.

## Complete publication admission

[Complete publication admission](research-complete-publication-admission.md) now
binds the entire research object and complete revision digest before validating
fixture render audits. This is step 3's pure boundary only: database publication,
historical re-render authorization and interchange integration remain open.

[Complete publication storage](research-complete-publication-storage.md) extends
step 3 with isolated schema-8 transactions and explicit historical re-rendering,
subject to required actual PostgreSQL and restore checks. Complete-history
export/import in step 4 remains open.

[Complete research bundles](research-complete-bundles.md) implement step 4's bounded
export and offline admission with complete predecessor/source closure. The version
is explicit and legacy bundles remain unchanged. Live-origin-authorized import,
recipient retention and deletion propagation for this new format remain open.

[Complete research import](research-complete-imports.md) extends step 4 with explicit
schema-9 operation/payload format binding and the shared recipient lifecycle.
Legacy payloads remain unchanged. Live-origin comparison, full-history admission,
retention and deletion propagation require actual database/recovery verification;
#71's declared scope is ready for final assessment only after those checks pass.
