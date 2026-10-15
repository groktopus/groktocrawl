# Construct Source-Bound Answer Units

- Status: rejected
- Deciders: Magnus Hedemark
- Date: 2026-09-10
- Scope: lean evidence-first successor experiment in `magnus919/groktocrawl-x`
- Plan: issue [#280](https://github.com/magnus919/groktocrawl-x/issues/280), W7
- Supersedes: none; narrows the first implementation of ADR-0068 and ADR-0069

## Context and Problem Statement

The first evidence-first replacement candidate asked a model to construct a whole
knowledge graph and then asked the same model route to review it. In the authorized
W1 comparison, it completed 39 of 150 attempts, compared with 77 for the incumbent.
Its median latency was 17,570 ms versus 2,189 ms and it used 15.7 times the reported
tokens. Its completed answers also had lower strict support and citation correctness.
It is rejected as the current replacement.

The experiment still supports the separation in ADR-0068 and the evidence rules in
ADR-0069. Completed candidate answers covered requested subquestions slightly better
than the incumbent. A successor should retain typed evidence, uncertainty, coverage,
and fail-closed publication while moving mechanical work into application code.

## Decision Drivers

- Improve completion, latency, support, and citation correctness together.
- Keep every published factual unit traceable to exact retained source text.
- Use model judgment only where semantic interpretation is required.
- Prevent generated identities, graph shape, and self-review from causing avoidable failures.
- Preserve room for richer planning and LangGraph workflows without coupling publication truth to a runtime.
- Require a new held-out packet after the previous packet became development evidence.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Tune the rejected whole-graph and full-review prompts | Reuses the current candidate | Preserves two large calls, high latency, fragile shape generation, and correlated self-review |
| Return to one free-form synthesis call | Small and comparatively fast | Gives up exact passage identity, explicit coverage, deterministic citations, and fail-closed eligibility |
| Extract source-bound answer units and assemble them deterministically | Retains evidence discipline with less model work | Requires a new bounded contract, assembler, and selective-review policy |

## Decision Outcome

Recommend the third option for a bounded successor experiment.

The application prepares immutable passages before inference. Each has an
application-assigned identity, snapshot identity, exact locator, digest, and source
metadata. One bounded construction call receives the question, required
subquestions, as-of constraint, and passages. It may return only small answer units
containing a concise factual or uncertainty statement, addressed subquestion IDs,
supplied passage IDs, necessary scope and qualifications, a semantic support
judgment, and conflict or insufficiency markers.

The model cannot create source, passage, citation, verification, or approval
identities. Unknown fields, dangling references, duplicate unit identities,
over-budget output, and invalid shapes fail closed. Generated prose is never a
source passage.

Application code resolves passages, constructs Knowledge IR records, measures
coverage, groups conflicts, orders units, assigns citation numbers, and renders the
artifact. It owns deterministic schema, digest, locator, reference, budget,
coverage, and citation checks. The renderer may join and format accepted units but
may not introduce new material factual assertions. Missing subquestions remain
visible instead of being filled with plausible prose.

A second model call is exceptional. A deterministic policy selects units for review
when they contain an inference, combine passages, depend on disputed evidence, carry
high-consequence guidance, or cross a pinned ambiguity threshold. Simple
source-attributed units receive no routine second call. The reviewer sees only the
unit, its passages, and required context; it cannot rewrite the answer or grant
human approval. Construction and review may use the same local route, but records
must disclose that dependence.

The contract is runtime-neutral. The imperative controller remains the reference.
LangGraph may coordinate future branching research, durable interrupts, checkpoint
forks, or specialized reviewers, while application contracts remain authoritative
for evidence, budgets, and publication. A workflow node cannot broaden authority or
bypass the publication gate.

The previous replacement packet is exposed development evidence. Freeze successor
code, policy, prompts, model route, and budgets before independent curation of a new
private held-out packet. Only that new packet can support the next adoption claim.

## Inherited Decision Impact

| Record | Relationship | Exact scope |
|---|---|---|
| ADR-0068 | Narrow first implementation | Retain execution, knowledge, and rendering boundaries; construct bounded units instead of a whole graph in one model response |
| ADR-0069 | Retain and allocate responsibility | Preserve evidence semantics; application code owns mechanical identities, structure, citations, and deterministic checks |
| ADR-0070 | Retain | Keep policy quality separate from runtime choice and require a newly isolated comparison packet |
| ADR-0073 | Retain | LangGraph remains an optional runtime under the same application contract |
| ADR-0075 | Retain | Consolidated formats remain the interchange target; answer units are construction input, not another public format |
| ADR-0076 | Defer | Retention cannot make a failed unit eligible |

No accepted predecessor is superseded. Acceptance would authorize only bounded
implementation and evaluation. It would not authorize production cutover, mainline
replacement, public API changes, storage selection, or a claim that this is better.

## Consequences

The common path uses one construction call and deterministic assembly. Complex or
risky units can receive focused review without doubling work for every answer.
Exact passage identities and application-owned citations remove two observed
failure classes. The design can publish explicit partial or uncertain results.

Smaller units can lose relationships a full graph might expose, and construction
can still omit evidence or make unsupported claims. Selective review can miss risk
if its trigger policy is poor. Explicit coverage and risk-trigger fixtures and a
fresh held-out comparison remain necessary.

## Confirmation

Before freezing, require tests for supported, conflicting, insufficient, stale,
multi-passage, inference, and high-consequence units. Prove that unknown passage
IDs, locator or digest mismatches, new renderer facts, missing coverage, and failed
or absent required reviews prevent publication.

Allow no more than one construction call plus one selective review call per attempt.
Pin tighter unit and token ceilings before the freeze. Measure calls, tokens,
latency, completion, support, coverage, citation correctness, and critical findings
with the W1 definitions. A fresh held-out comparison decides continue, revise, or
stop; fixture success alone does not accept this architecture for production.

## Links

- [W7 Candidate D outcome](../../../experiments/enterprise-evaluation/w7-candidate-d-outcome-2026-09-11.md)
- [W1 comparison outcome](../../../experiments/enterprise-evaluation/w1-comparison-outcome-2026-09-10.md)
- [W1 semantic learning review](../../../experiments/enterprise-evaluation/w1-semantic-learning-review-2026-09-10.md)
- [ADR-0068](0068-separate-research-execution-knowledge-and-rendering.md)
- [ADR-0069](0069-define-versioned-knowledge-and-verification.md)
- [ADR-0070](0070-evaluate-research-policy-and-runtime-separately.md)
- [ADR-0075](0075-consolidate-research-interchange-contracts.md)

## Rejection evidence

The authorized held-out comparison completed on 2026-09-11. Candidate D
completed 4 of 150 scheduled attempts, compared with 134 for the incumbent, and
its median attempt latency was 2.30 times the incumbent. The four completed
answers were insufficient to establish a semantic-quality advantage. Candidate D
therefore failed the completion, median-latency and comparative-quality gates.

This rejection applies to the frozen Candidate D implementation. It does not
reject the accepted separation of execution, knowledge and rendering in ADR-0068
or the evidence semantics in ADR-0069. Application-owned source identity,
deterministic citations, visible missing coverage and runtime-neutral publication
remain valid inputs to a future design. Any successor requires a new ADR and a
fresh comparison; this record does not authorize deployment or replacement.
