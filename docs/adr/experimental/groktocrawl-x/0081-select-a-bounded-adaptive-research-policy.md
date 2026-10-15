# Select a Bounded Adaptive Research Policy

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-12
- Scope: experimental research architecture in `magnus919/groktocrawl-x` only
- Plan: W10; issue [#314](https://github.com/magnus919/groktocrawl-x/issues/314)
- Extends: [ADR-0070](0070-evaluate-research-policy-and-runtime-separately.md)

## Context and Problem Statement

W8 found that model-generated follow-up queries could discover useful sources,
but its planner searched on every case, admitted substantially more weak material,
and could not judge when the initial evidence was sufficient. Query formulation
alone is therefore not a safe research policy.

W10 compares fixed retrieval with four increasingly controlled adaptive policies.
The controls bind every follow-up to a declared evidence gap, reject weak query
proposals, admit sources only for declared marginal value, and stop from recorded
state. The study separates the W8 reproducibility anchor from challenge cases for
unsupported claims, contradictions, authority or currency gaps, publisher
independence, and entity ambiguity.

The criteria were fixed before the arm map was revealed. The complete study now
contains 540 of 540 planned trials and 4,487 of 4,487 blinded adjudications.

## Decision Drivers

- Close important evidence gaps that fixed retrieval leaves open.
- Avoid unsupported high-importance claims and weak-source dilution.
- Spend additional searches and model calls only for a named, observable need.
- Make proposal admission, source admission, and stopping reproducible from
  retained state.
- Preserve bounded execution and a fixed-query fallback.
- Limit any adoption to the case types supported by matched repeated evidence.

## Considered Options

| Option | Benefit | Cost or boundary |
|---|---|---|
| Keep fixed retrieval | Lowest latency and simplest predictable path | May leave recoverable evidence gaps open |
| Adopt bounded recovery for named gap types | Spends extra work only where the study shows a repeatable benefit | Requires deterministic triggers and leaves other cases on fixed retrieval |
| Adopt the full bounded policy for the tested domain | Applies proposal gating, marginal-value admission, and deterministic stopping consistently | Adds search and model work and requires every frozen gate to pass |
| Run another experiment | Preserves uncertainty when one decision-critical question remains | Delays policy adoption and must name evidence that would change the decision |

The unconstrained W8-style planner is an ablation arm, not an adoption candidate,
unless new evidence overturns its observed inability to stop selectively.

## Decision Outcome

Keep fixed-query retrieval as the default. No tested challenge type qualified
for adaptive recovery, so this decision selects no adaptive trigger, proposal
rule, admission rule, or stopping policy for the product path.

The full bounded policy failed the frozen non-inferiority gate on the reused W8
anchor: weighted claim closure fell from 86.4% to 53.0%, a 33.3 percentage-point
loss. Precision fell 1.5 points, remaining within that individual margin, but
only 11.1% of follow-up queries produced a declared evidence gain and 88.9% were
executed after the final gain. The full policy also failed every challenge-type
replacement gate in all three repetitions. All 540 trials completed within the
hard bounds, so the result is about research value rather than transport failure
or budget enforcement.

The independent blinded review covered 4,211 source judgments and 276 claim
closures. Agreement was imperfect, but applying those judgments as the declared
sensitivity produced the same `retain_fixed_default` decision. Equal claim
weights also preserved the failed anchor gate. One of twelve leave-one-case-out
checks selected `missing_primary` before applying the anchor gate, showing a
narrow case-composition sensitivity that is insufficient for adoption.

This decision does not reject future adaptive research. A new policy may be
reconsidered when it changes the control mechanism rather than merely retuning
the failed policy and then clears the same matched value, precision, unnecessary
work, failure, and anchor non-inferiority gates. Promising directions include
using deterministic evidence-state changes to trigger continuation, requiring
observed retrieval receipts before spending another query, or using SlopSearX's
bounded continuation workflow while GroktoCrawl retains ownership of research
state and budgets.

Policy 5 may replace fixed retrieval for a named challenge type only when, in that
stratum and at least two of three repetitions, it improves weighted claim closure
by at least 10 percentage points, leaves no more unsupported high-importance
claims, keeps admitted-source precision within 5 percentage points of fixed
retrieval, executes at most 10% unnecessary queries after the final evidence gain,
stays within every work bound, and has no higher failure rate. Across the W8
anchor, closure and precision may each regress by no more than 2 percentage points.
If no policy clears every applicable gate, fixed retrieval remains the default.

## Consequences

The experimental stack retains fixed-query SlopSearX retrieval as its default.
W11 may now use that path as its frozen control while measuring SlopSearX's
recorded continuation substrate. W11 measures substrate capability; it cannot
silently adopt the W10 policy or reinterpret this result as approval for
unbounded autonomous search.

The explicit gap, proposal, candidate-disposition, marginal-value, stop, and
budget records remain useful experimental contracts. They explain failures and
make future policies comparable even though this policy was not selected.

## Confirmation

The [W10 evidence package](../../../experiments/evidence/adaptive-policy/w10-final/00-index.md)
contains the secret-free adjudication record, primary and adjudicated sensitivity
analyses, full accounting dossier, Artifact Pyramid, competing-hypothesis matrix,
pre-mortem, and outcome report. Its retained private packet matches the frozen
manifest and accounts for every query, candidate, exclusion, source, failure,
and adjudication. The public accounting reports 929 executed queries, 6,736
candidate records, 3,595 acquired candidates, and 5,133 source-to-claim links.

CI confirms the record and its public artifacts are internally consistent. The
decision is reviewed in the W10 pull request. Acceptance applies only to the
experimental fork and does not change GroktoCrawl mainline or its production
deployment.

## Links

- [W10 research brief](../../../experiments/adaptive-policy/w10-research-brief.md)
- [W10 frozen protocol](../../../experiments/adaptive-policy/w10-frozen-protocol.md)
- [W10 analysis plan](../../../experiments/adaptive-policy/w10-analysis-plan.md)
- [W10 research log](../../../experiments/adaptive-policy/w10-research-log.md)
- [W8 adaptive planning outcome](../../../experiments/enterprise-evaluation/w8-adaptive-planning-outcome-2026-09-11.md)
- [ADR-0070](0070-evaluate-research-policy-and-runtime-separately.md)
- [ADR-0073](0073-compare-research-runtimes-under-one-policy.md)
