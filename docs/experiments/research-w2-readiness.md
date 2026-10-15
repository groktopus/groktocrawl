# W2 fixture readiness checkpoint

> **Historical checkpoint.** This document records the W2 state when it was
> written. ADR-0074 and ADR-0079 were accepted later, and provider-backed
> evaluations were subsequently authorized. See the
> [GitHub execution tracker](research-architecture.md#github-execution-tracker)
> for current status. The limitations below remain useful evidence about what
> this bounded fixture did and did not establish.

Scope: experimental GroktoCrawl X only; not a mainline replacement. Foundation
ADRs 0067–0070 and 0072 are accepted for bounded fixtures. Storage 0071 is accepted for bounded experimental exploration per issue #47;
runtime adoption 0073 and recovery 0074 were proposed at this checkpoint. This checkpoint accompanies issue
[#31](https://github.com/magnus919/groktocrawl-x/issues/31).

## Current conclusion

An executable local fixture journey now acquires text, constructs an immutable
structural revision, binds explicit assessment/verification/freshness records and
publishes an audited summary, analysis and dossier. The controller owns budgets,
cooperative deadlines, cancellation and terminal outcomes. Complete fixture history
is also retained and validated across publication, re-render, export and
same-authority import in the isolated PostgreSQL adapter. These are executable
contract checks;
W2 is **not complete**, and the internal formats are not complete `knowledge-ir/1`.

The fixture author supplies claim annotations, assessment outcomes, semantic
verdicts and render wording. A passing test establishes that the implementation
honors those supplied expectations, not that the expectations are true, independent
or human-reviewed. Hosted integration proves regression compatibility, not research
quality. No provider has been added. Subsequent isolated PostgreSQL storage slices are
recorded below.

## Requirement-to-evidence matrix

| Accepted requirement | Implemented fixture evidence | Remaining limit or gate |
|---|---|---|
| ADR-0068: separate execution, knowledge and rendering | `pipeline.py` uses controller-owned acquire/construct/verify/render stages; bodies stay outside `ExecutionState`; `test_fixture_pipeline.py` | Finite local plan only. No independently evaluated research policy, adaptive search, bounded semantic repair loop or provider adapter. |
| ADR-0068: ownership, budgets, deadlines, cancellation | `execution.py`, `controller.py`; ledger/controller/pipeline tests exercise reserve-before-dispatch, stable receipts, late-result rejection and no failed/cancelled publication | Cooperative callbacks are trusted; blocking or cancellation-suppressing code cannot be forcibly stopped. No restart safety, leases or persistent ownership. |
| ADR-0069: exact evidence, identity, relationships | `knowledge.py`; Unicode spans, digests, scoped IDs, edge references and derivation DAG tests | Inline text fixtures only. Observation labels, semantic equivalence and source authenticity are not inferred or authenticated. Binary locators remain unspecified. |
| ADR-0069: freshness, source dependence and uncertainty | Source dates/provenance, lineage/origin, as-of context and historical/current scope; `test_source_freshness.py` | Dates and lineage are recorded assertions. No independent-source count or proof of current real-world truth. |
| ADR-0069: assessment distinct from verification | `FixtureAssessment` and explicit `AssessmentLink` mapping in `FixtureVerificationSet`; `test_fixture_assessments.py` | Fixture assessor shares the configured fixture identity contract. No real assessor authentication or independently evaluated semantic assessor. |
| ADR-0069: verification and publication eligibility | Input-bound support/freshness/conflict records plus exact three-layer audit binding; assessment alone cannot publish | Fixture verdicts can be wrong. No demonstrated entailment, caveat preservation or unbiased semantic audit beyond hand-authored cases. |
| ADR-0069: revision identity and append-only records | `revisions.py`; typed introductions, immutable IDs across removal/reintroduction, historical/current record separation | Bounded complete linear history is persisted and portable through same-authority bundles. Authorship is not authenticated; declared novel/replacement semantics cannot be inferred. |
| ADR-0069: complete versioned IR | Separate structure, verification, publication and revision prototype formats implement substantial parts | Consolidated schema/examples and compatibility policy are not frozen as `knowledge-ir/1`. Claim-to-assessment links currently live in an explicit envelope mapping. Retained envelopes use JCS; inner fixture hashes retain their original rules. The consolidated IR and render-manifest layouts remain open. |
| ADR-0070: separate policy/runtime comparisons, frozen evidence, negative controls | Inherited deterministic regression baseline is pinned in `research-preflight.json`; fixture contract negative tests are executable; the sealed W1 packet is eligible and the incumbent answer observation is recorded in [the baseline input record](enterprise-evaluation/w1-baseline-inputs-2026-09-08.md) | Human semantic labels, executable candidate arm definitions, numerical bounds, and comparison authorization remain unresolved. Current fixture tests are not a completed A/B/C study. |
| ADR-0072: verified final output distinct from progress, stable identity and coverage | Internal controller final outcome, same-revision artifacts and complete/partial/insufficient coverage | No experimental HTTP/SSE, CLI/MCP, reconnect/replay or authorization implementation. Public delivery remains later W6 work. |

Implementation paths above are under `agent-svc/agent/experimental/`; test paths are
under `tests/unit/`. See the [implementation record](research-architecture.md) for
merged milestones and the issue/PR for the current CI evidence. Tests do not prove
requirements listed as remaining limits.

## Concrete next gate

Do not turn fixture test counts into an adoption decision or start an unfrozen
comparison. Prepare a reviewed evaluation design and a final schema gap disposition
before moving beyond the bounded fixture scope.

| Required input or decision | Proposed reviewable action | Decision owner / constraint |
|---|---|---|
| Target research workload and corpus | Choose a representative domain/question mix; freeze separate development and held-out source snapshots/questions, denominators and expected negative/abstention categories | Magnus selected enterprise agentic engineering/software factories. The 30-case packet is sealed, validated and isolation-approved; raw contents remain private. |
| Independent semantic reviewers | Name primary and adjudicating reviewers and define blinded rubric/adjudication for support, scope, conflict, freshness and render additions | Magnus authorized Hermes as a separate AI reviewer and retains human calibration/adjudication. Held-out output labels are not collected yet; one-human limitations remain explicit. |
| Measurement thresholds and resources | Record quality/latency/resource regression bounds with rationale, hardware, run budgets, seeds/order and uncertainty plan | Must be explicit before applicable comparisons. Existing protocol minima are not measured results or an authorized series. |
| Complete IR contract | Review each prototype field against ADR-0069, freeze the consolidated schema and reader/version policy, and resolve D3 interchange dependencies | Keep prototype names until this is complete. See the current field/version review below; isolated storage exploration is already accepted. |
| Provider-backed execution, if needed | Specify local/provider model and exact spending ceiling before connecting a real verifier or running it | At this checkpoint, the external-provider budget was zero and no provider work was authorized. |
| Runtime/storage/recovery decisions | Use accepted ADR-0071 exploration gates; ADR-0073/0074 were proposed at this checkpoint | Preserve the pgvector-versus-Qdrant consolidation evaluation and conditional PostgreSQL-native recovery option; PostgreSQL exploration was approved, not production/vector adoption. |

The authoritative unresolved fields remain in
[`research-preflight.json`](research-preflight.json). Null means unresolved; this
checkpoint does not fill them with permissive defaults, promote the regression
baseline or authorize comparisons. The packet and observed incumbent baseline are
now frozen inputs; the next step is to resolve the semantic grading, bounds and
execution-control fields as one reviewable decision packet.

## Evaluation design follow-up

Magnus supplied the domain and reviewer direction after this checkpoint. See the
[enterprise evaluation design](enterprise-evaluation/README.md), tracked in issue
[#33](https://github.com/magnus919/groktocrawl-x/issues/33). The synthetic calibration
corpus and separate Hermes design review do not resolve the human calibration,
measured bounds or comparative-execution gates.

## Canonical admission implementation

[Bounded canonical JSON admission](research-canonical-admission.md) implements
representation checks and JCS bytes for the approved storage exploration. It does
not freeze the complete IR schema or establish database lifecycle behavior.

The [isolated PostgreSQL harness](research-postgres-harness.md) introduces a real
database CI boundary for storage exploration. Its transport probes do not freeze
the research schema or complete the W3 lifecycle matrix.

The [retained-source staging adapter](research-source-storage.md) adds bounded
source transactions and receipts. Complete Knowledge IR remains unfinished; the subsequent fixture publication
extension is recorded below. Source staging alone does not complete W2 or W3.

[Retained structural revisions](research-retained-revisions.md) now pin scoped
source references and a parent chain transactionally. Complete Knowledge IR and
authenticated semantic verification remain separate unfinished gates.

The [retained fixture publication slice](research-retained-publications.md) adds
atomic audited outputs and pinned reference reopening on isolated schema 3. This
remains synthetic fixture evidence and does not complete W2/W3 or human calibration.

Explicit historical re-rendering now preserves the complete original research
envelope while creating a new audited fixture presentation on schema 4. This adds
reuse without retrieval; the remaining W3 gates are still open.

[Bounded artifact bundles](research-artifact-bundles.md) now support consistent
export and offline integrity validation with preserved original identities.
[Same-authority scoped imports](research-import-contract.md) now preserve those
bytes in recipient mappings, with atomic origin revocation on schema 5. PR #67
passed all eleven checks and all five post-merge workflows; actual PostgreSQL CI
ran 63 storage cases and restored nine retained imports while denying deleted
copies. These are fixture lifecycle results, not remote authorization or W3 completion.

The [bounded expiry collector](research-expiry-collection.md) adds an explicit
internal method for payload/quota reclamation on schema 6, with real database
race and restore cases required in CI. No background schedule or production
recovery service is introduced. The later complete-history, admission and capacity
checkpoints are recorded below.
Consolidated IR and independent semantic evaluation remain open.

## Complete revision compatibility follow-up

The [field and behavior disposition](research-ir-compatibility.md) records completed
issue [#71](https://github.com/magnus919/groktocrawl-x/issues/71): complete retained
fixture history now supports publication, re-render and bounded interchange.
[Connection admission](research-storage-admission.md) (#80) and the
[measured capacity checkpoint](research-storage-capacity-findings.md) (#82) are also
complete within their documented experimental scope.

The next contract work is the [Knowledge IR field and version review](research-ir-contract-review.md)
(#86): create an executable inventory and compatibility examples, then define the
consolidated IR and render-manifest layout. No prototype is renamed to
`knowledge-ir/1`; W2/W3 and independent semantic evaluation remain open.
