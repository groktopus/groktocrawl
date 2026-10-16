# Define Versioned Knowledge and Verification

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-04
- Scope: experimental research Knowledge IR, independent of storage/runtime
- Plan: D2 / W1; issue [#3](https://github.com/magnus919/groktocrawl-x/issues/3)
- Supersedes: experimental scope only, as classified in the inherited-impact table; inherited endpoints remain unchanged
- Experimental successor: Extended by ADR-0075 for consolidated layout, explicit fields, source references, assessment mapping and reviewer input contracts. [ADR-0075](0075-consolidate-research-interchange-contracts.md), accepted 2026-09-06.

- Accepted: 2026-09-05 by Magnus Hedemark, following the W1 review packet.
- Acceptance scope: foundation contracts for a bounded fixture-backed prototype;
  implementation/evidence gates remain. No storage/runtime/recovery selection or
  external-provider spending is authorized by this acceptance.
- Acceptance record: [issue #15](https://github.com/magnus919/groktocrawl-x/issues/15).

## Context and Problem Statement

A source URL and a citation marker identify a document but not the passage or
reasoning that supports a conclusion. Source pages change, copies masquerade as
independent corroboration, dates may be missing, and a summary can omit decisive
caveats. Research needs a portable representation whose support can be inspected
without treating generated prose, retrieval ranking, or model confidence as evidence.

[ADR-0068](0068-separate-research-execution-knowledge-and-rendering.md) separates
execution, knowledge and rendering. This ADR proposes the minimum semantic contract;
W2 will implement typed models and executable schema validation. The example is a
design fixture, not proof that those validators already exist.

## Decision Drivers

- Resolve each claim to an immutable evidence version and precise supporting context.
- Distinguish what a source states, what is observed, and what is inferred.
- Preserve contradictions, uncertainty and source dependency through all renderers.
- Make changes to claims, verifiers and freshness judgments auditable.
- Support interoperable JSON without selecting a graph database or Python framework.

## Considered Options

| Option | Advantages | Disadvantages |
|---|---|---|
| Prose plus source URLs | Small, familiar output | No durable passage support or explicit conflict/derivation model |
| Extracted facts with a confidence score | Compact, easy ranking | Conflates source confidence, model belief, freshness and verification |
| Versioned claims, snapshots, relationships and verification records | Auditable, portable and supports multiple renderings | More storage and validation; semantic judgment still fallible |

## Decision Outcome

Propose the third option as `knowledge-ir/1`. A JSON envelope represents an immutable
revision. Identifiers are opaque within an artifact scope; hashes prove byte identity,
not truth or global uniqueness of a claim. No automatic entity-resolution or
cross-research claim deduplication is required in v1.

### Minimum entities

| Entity | Required contract |
|---|---|
| IR revision | `schema_version`, `research_id`, `revision_id`, nullable `parent_revision_id`, `created_at`, objective, question/as-of constraints, policy version, claim/evidence/relationship/verification collections, coverage and unresolved questions |
| Snapshot descriptor | `snapshot_id`, canonical URL, retrieval time, content digest and normalization version, media type, resolvable immutable content reference; publication/effective dates nullable with provenance; origin/lineage IDs where known |
| Evidence | `evidence_id`, snapshot ID, exact locator, quoted span or digest of that span; locators refer to the same normalized content version |
| Claim | `claim_id`, text, kind (`source_statement`, `observation`, `inference`), question/scope qualifiers, evidence assessment and verification record IDs |
| Relationship | `relationship_id`, type (`supports`, `contradicts`, `derived_from`), referenced endpoint IDs and scoped rationale; specify direction and derivation rule/assumptions |
| Verification | `verification_id`, subject ID, check type, verdict (`pass`, `fail`, `indeterminate`), verifier identity/version, policy/prompt/model identity where applicable, checked input digest, time, bounded reason and evidence references |
| Render manifest | Artifact set ID, pinned IR revision, renderer/version, summary/analysis/dossier artifact IDs/digests and statement-to-claim mappings; render audit record references |

Use UTC timestamps with explicit offsets. Unknown publication/effective dates are
null, never replaced with retrieval time. The source freshness policy records its
basis and evaluated time; storage TTL is not the age of a real-world fact.

For v1 text locators, use zero-based, half-open Unicode code-point offsets into the
exact normalized text, plus an exact quote. Hash its UTF-8 bytes. Record the
normalization version and snapshot digest so normalization cannot silently move a
span. Binary/PDF evidence requires a separately specified representation and locator
before it is admitted; do not pretend a Markdown offset locates a PDF byte range.

Within one research artifact, IDs are never reassigned. An unchanged entity can
keep its ID across revisions; changed text, scope, evidence content or semantic
meaning gets a new entity ID with a predecessor link. Old revisions retain their
original entities. Verification is append-only; changed input or verifier identity
requires a new verification record. D3 determines serialization/canonicalization
for whole-envelope hashes before implementations exchange digests.

### Relationship and evidence semantics

`supports` and `contradicts` connect evidence → claim; `derived_from` connects a
claim → its premise claim(s), with a rule and assumptions. A conflict record can
group mutually incompatible claims and evidence under a shared scope. Derivations
form a directed acyclic dependency graph; reject dangling IDs and cycles. Do not
infer transitive support automatically: each derived conclusion needs its own
verification against premises, assumptions and relevant source context.

A source statement means a particular source says something. It can be supported
while the underlying real-world proposition remains disputed. `observation` is
restricted to a directly recorded acquisition/tool/measurement result; do not label
LLM inference an observation. Copied pages share lineage where known; unknown
independence must not be scored as confirmed independent corroboration.

Claim evidence assessment uses explicit states: `unassessed`, `supported`,
`contested`, `insufficient`, or `refuted`, plus assessment record IDs. Here
`supported` means supported under the recorded evidence and policy, not proven
true. Keep verification outcome separate from evidence assessment: a semantic
verifier may fail or be indeterminate even when structural checks pass.

### Verification and publication eligibility

Perform these separately and preserve their outcomes:

1. **Structural:** schema/version, unique identities, all references resolve, digest
   matches, locator bounds and quote equality, valid relation kinds and acyclic
   derivations. These checks are deterministic and fail closed.
2. **Support:** determine whether the quoted span in its surrounding context supports
   the scoped claim, including units, time and conditions. Source URL existence,
   quote equality and retrieval relevance do not establish entailment.
3. **Conflict and coverage:** record contradictory evidence, dependence among sources,
   missing required subquestions, unknown dates and unresolved alternatives.
4. **Render audit:** map every material factual assertion to eligible claims in the
   pinned revision; preserve qualifications and conflicts, check citation identity,
   and identify unsupported assertions introduced by wording or omission.

A normal factual assertion is eligible only with passing structural and applicable
support checks, no unaddressed scope conflict, and an applicable freshness judgment.
Contested/insufficient claims may appear only as explicit uncertainty or source-
attributed reports; the qualification itself must be grounded and audited. Audit
outputs must distinguish a machine-evaluated pass from a human-reviewed pass. No
LLM may set `human_reviewed` or assert that another reviewer approved its work.
Optional numerical confidence is diagnostic only until calibrated; it cannot
replace these states or independently authorize publication.

Do not discard negative evidence to make an artifact pass. A legitimate final
artifact may report that an answer could not be established. If a source contains
instructions to change the workflow, treat them as untrusted content, never tool
or policy authority. Limit repairs by the execution budget in ADR-0068.

### Reuse, retention and change

IR revisions and snapshots are separately addressable from expiring sessions and
model contexts. D3 chooses retention windows, access control, deletion/tombstones,
quota, garbage collection, export/import and restoration. This ADR does not promise
infinite retention. Within the declared window, committed references must resolve;
a missing/deleted source causes a visible unavailable-evidence result, not silent
substitution of newer content under the old identity.

Re-rendering an old revision answers from its recorded evidence. To claim freshness
at a later date, evaluate the freshness policy and acquire new snapshots if needed.
Changed knowledge or verification creates a new revision. A cached prose answer
cannot be imported as verified knowledge without the construction/verification
pipeline and retained evidence. Preserve authorization scope during reuse/export;
semantic similarity must not bypass source access or request constraints.

## Inherited Decision Impact

| Record | Intended relationship on adoption | Exact scope |
|---|---|---|
| ADR-0004 | Extend at research boundary | Adapter Markdown/metadata remains an acquisition result; snapshot identity and evidence locators are added downstream |
| ADR-0016 | Retain and extend | Content extraction gates remain useful but do not count as claim-support verification |
| ADR-0017 | Partially supersede for experimental answers | Claim/evidence mapping becomes authoritative; citation parsing is a presentation adapter, not the support model |
| ADR-0024 (proposed) | Extend | Same three output layers, grounded in one revision with audit mappings |
| ADR-0041 (proposed), 0049 | Replace prose-as-knowledge assumption; retain compatibility intent | Reuse of verified knowledge requires evidence/revision/verifier/freshness compatibility; D3/D6 decide new cache/storage/wire details |
| ADR-0050, 0059 | Extend | Request/pass reuse remains; immutable snapshots and IR identities have an independent lifecycle |
| ADR-0064 | Partially supersede | One final synthesis becomes construction, verification and audited rendering; do not emit multiple unmarked answers |

Predecessor metadata records scoped experimental successors. Storage and recovery
selection remain separate decisions; ADR-0072 defines the accepted client direction.

## Consequences

Reviewers can locate exact support, inspect derivations and retain disagreement.
Renderers share a common knowledge basis. The representation adds storage, schema
migration and verification costs. Model-assisted support checks still make errors;
independent evaluation is essential. Premature extraction can omit context, so
verification must resolve surrounding source content, not only isolated quotes.

## Confirmation

W2 must implement typed/schema validation and positive/negative fixtures for every
structural invariant, support category, conflict and render mapping. The research
owner provides examples where quote equality passes but semantic support fails,
where two copied sources count as one lineage, and where a qualified disagreement
is publishable but an unconditional conclusion is not. W3 adds expiry/restore and
access-scope tests. ADR-0070 supplies independent semantic evaluation. Missing or
indeterminate required verification blocks ordinary factual publication.

Magnus reviews the schema examples and all scoped predecessor impacts before
acceptance. Revisit at new evidence media types, schema migrations, semantic verifier
changes or incidents showing unsupported output passed the gate.

## Links

- [Execution boundaries](0068-separate-research-execution-knowledge-and-rendering.md)
- [Evaluation contract](0070-evaluate-research-policy-and-runtime-separately.md)
- [Worked example](../../../experiments/research-foundation-example.md)
- [Plan: D3 storage and D6 transport follow-ups](../../../experiments/research-architecture.md#architecture-decision-work)
