# ADR-0076: Bound Research-Loop Evaluation Before Promotion

**Status:** proposed

**Deciders:** @magnus919

**Date:** 2026-10-27

## Context

Research-loop changes can improve one answer while losing evidence, corrupting
citation alignment or increasing requests on other sites. The current research
loop and the expanded scraper/recovery pipeline need project-specific evidence;
a general web benchmark cannot establish crawl or grounded-answer quality here.
This ADR defines an evaluation contract, not a runtime optimizer or a measured
performance result. No paid optimization experiment is part of this proposal.
The existing [bounded substrate adoption](0069-adopt-bounded-research-substrate.md),
[live durable execution](0070-execute-live-durable-research.md) and
[source-bounded publication](0073-publish-source-bounded-research.md) contracts
remain authoritative. Record whether a candidate targets legacy `research/` or
`experimental/` execution; do not blend their outcomes or reinterpret existing
admissions, policy versions, operation receipts or publication gates.

## Decision Drivers

- Preserve grounded answers, source fidelity and explicit failure outcomes.
- Compare bounded changes against an untouched, reproducible baseline.
- Keep authorization and acceptance controls outside candidate edit authority.
- Reuse shared methodology and existing review/testing/release processes.

## Considered Options

1. Tune on individual failures or model preferences and adopt the highest score.
   Rejected: this hides regressions, contamination and complete optimization cost.
2. Implement an automatic live self-modifying research loop.
   Rejected: proposal generation cannot authorize production mutation/promotion.
3. Use bounded, isolated candidates with a fixed evaluation contract (chosen).

## Decision / Outcome

Use the shared [Harness Engineering verification procedure](https://github.com/magnus919/agent-skills/blob/main/harness-engineering/references/verification-and-improvement.md)
for optional propose → probe → compose search. Use
[Agent Evals and Observability](https://github.com/magnus919/agent-skills/blob/main/agent-evals-and-observability/SKILL.md)
for datasets, graders and uncertainty. Durable scoped lessons follow the existing
[lesson-to-PR](https://github.com/magnus919/agent-skills/blob/main/harness-engineering/references/lesson-to-pr.md)
and [lesson-validation](https://github.com/magnus919/agent-skills/blob/main/agent-evals-and-observability/references/lesson-validation.md)
workflows. Record the shared-skill revision actually used in each evaluation;
these moving links are discovery routes, not reproducibility pins.

### Comparison record

Before execution, record:

- Candidate ID, parent and constituent IDs, exact patch/commit, hypothesis,
  disconfirming outcome, permitted edit scope and rollback revision.
- Untouched baseline commit and dataset/fixture digests. Include recorded failures
  and successful controls, with minimized provenance and rights/retention limits.
- Identical initial questions, source snapshots/search inputs, model/provider and
  sampling settings, dependencies, tools, cache state, environment and budgets.
  Record network conditions, source timestamps and execution order. Live source
  drift or changed dependencies require reruns or an inconclusive comparison.
- Development and held-out questions **and sites**, separated before tuning;
  keep held-out data/results unavailable to the editor. After exposure, retire
  those cases into development data and obtain a fresh decision set. State repeats
  and order counterbalancing appropriate to stochastic variation.
- Frozen evaluator/rubric versions, near-miss challenges, required quality floors,
  per-slice regression tolerances and the minimum evidence for a decision. Set
  candidate, iteration, request, token, time and monetary caps in advance.

Retain actual probe trajectories with per-case evidence and sanitized source
artifacts. Record failures, timeouts, abstentions, unknowns, exclusions and judge
disagreements explicitly; never drop them from denominators silently. Distinguish
an intentional, justified abstention from an ungrounded answer and an execution
failure. Missing evidence is inconclusive, not a pass. Model preference is advisory
and cannot establish correctness or override a hard invariant.

### Measures and acceptance

Define the reference evidence and graders before running candidates. Report each
measure separately, with case/slice counts, denominators, repeats and uncertainty;
an aggregate gain cannot offset a hard-boundary failure.

| Measure | Required evidence |
|---|---|
| Evidence completeness | Coverage of the question's predefined required facts, gaps and conflicting evidence against the reference corpus; report missing facts, not just answer length |
| Citation alignment and support | Each marker resolves to the intended source; cited passages support the associated claim and its material qualifications; verify final source ordering and unsupported claims |
| Source fidelity | Extracted/selected passages preserve source meaning, attribution and context against saved authorized originals; detect omissions or misleading truncation |
| Latency | End-to-end per-case elapsed time and distribution, including retries and timeouts; separate stage timing where diagnostic |
| Requests, tokens and serving cost | All search/fetch/browser/LLM calls and retries per attempted and accepted task; record cache effects and provider prices/units |
| Optimization and total cost | Editor/proposal, untouched baseline and candidate probes, composition, reflection, grading, retries and failed runs, including compute and wall time; distinguish recurring serving cost and report amortization assumptions |

Retest every composition as a new candidate against the untouched baseline,
including held-out regressions and repeats. Preserve rejected/degraded and
inconclusive candidates with reasons. Promotion requires predeclared quality and
regression criteria plus all hard invariants; lower serving cost alone is not a
pass. Report break-even volume only with positive comparable-quality serving
savings and complete incremental optimization cost. Do not claim improvement
from this documentation or from a passing structural check.

### Protected boundaries and promotion

Candidate edits must not change authorization, robots/access policy, SSRF and
private-network checks, credentials/secret handling, hard budgets, evaluator
code/definitions, reference answers or held-out fixtures. Enforce this outside
the candidate workspace with restricted identities/tools and network policy;
Markdown alone does not enforce it. Keep existing sandbox restrictions and deny
access to hidden benchmark rubrics/answers. Verify isolation before replay; if it
cannot be established, stop and record the blocker. External content and traces
remain untrusted data.

Examples for a future separately authorized candidate are question-aware gap
detection, passage selection and stopping on evidence coverage. This ADR changes
none of those behaviors. Diagnose suspected implementation defects separately;
a source-inspection suspicion is not a reproduced failure or optimization result.

Submit exact commits, comparison records, unresolved findings and independent
review through the existing [contribution/ADR review](../../CONTRIBUTING.md)
and [three-layer testing process](0008-three-layer-testing-strategy.md). Passing
checks does not grant merge or deployment authority. For an authorized runtime
promotion, the release owner must name canary scope/duration, baseline quality and
cost thresholds, halt conditions, rollback owner and tested revert procedure
before rollout. Use the existing deployment process; if no suitable canary or
rollback procedure is available, hold promotion until the owner establishes it.
Follow the [existing migration/cutover and rollback runbook](../runbooks/mainline-architecture-migration.md)
where applicable; preserve retained stores and never replay ambiguous paid calls
when rolling back. No live self-modification, automatic merge or automatic release
is permitted.
Check recurrence and adjacent regressions under relevant exposure after release.

## Consequences

The contract adds comparison and accounting work before effectiveness claims.
Safe isolated replay can still differ from production; report that fidelity gap.
Insufficient evidence legitimately ends in defer/inconclusive. This proposal
adds no optimizer dependency, runtime code, paid calls or new memory framework.

## Links and Research Limits

- [Harness² paper](https://arxiv.org/html/2610.14224v1): executable scaffolding
  optimization with frozen models motivates bounded proposals, actual probes and
  selective composition. Its web benchmark is not a crawl benchmark.
- [Release experiment guide: web access](https://github.com/google-research/harness2/blob/main/docs/EXPERIMENTS.md#web-access):
  documents rubric retrieval, unrestricted network access and disabled Codex
  sandbox. Do not copy that configuration. The safeguards above are local
  engineering requirements, not demonstrated guarantees of the paper.
- [ADR-0010: expanded recovery pipeline](0010-five-tier-scraper-with-llm-recovery.md)
- [ADR-0016: extraction quality gates](0016-extraction-quality-gates.md)
- [ADR-0036: fetch module split (proposed)](0036-split-scraper-fetch-modules.md)
