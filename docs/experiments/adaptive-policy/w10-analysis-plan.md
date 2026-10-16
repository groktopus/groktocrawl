# W10 adaptive-query analysis plan

Status: **declared before the corrected valid execution**

This plan defines how the refrozen W10 results will be interpreted. It adds no
cases, outcomes, or decision gates. The methodology audit exposed a mismatch in
the original stopping implementation; the invalid run was preserved and the
protocol was corrected before a valid execution. The purpose is to prevent a broad
claim such as "adaptive search works" from hiding the conditions that made it
work or fail.

## Questions the analysis must answer

1. **Does adaptation help?** Compare the full bounded policy with fixed
   retrieval on claim closure, admitted-source precision, failures, and work.
2. **Which control creates the value?** Use the five arms as an ablation:
   unconstrained formulation tests query generation; gap binding adds an
   explicit research need; proposal gating tests whether weak follow-ups can be
   rejected; marginal-value admission and deterministic stopping test whether
   the system can avoid dilution and excess work.
3. **When does it help?** Report effects separately for unsupported claims,
   contradictions, authority or currency gaps, and long-tail anchor cases.
4. **When does it hurt?** Identify query drift, repeated intent, derivative
   sources, weak source admission, late stopping, timeouts, and grader failures.
5. **Can the trigger be trusted?** Report how often a policy proposes a
   follow-up, how often the proposal gate accepts it, and how often the accepted
   query produces a new weighted closure or another declared evidence gain.

## Boundary measures

The final report must include these conditional measures in addition to the
frozen replacement gates:

- proposal yield: accepted proposals divided by proposals made;
- useful-query yield: executed follow-ups producing a declared evidence gain
  divided by executed follow-ups;
- gate precision: accepted proposals producing a declared evidence gain divided
  by accepted proposals;
- gate rejection audit: rejected proposals that would have produced a declared
  evidence gain, where the retained search evidence permits that counterfactual
  to be tested;
- stop efficiency: queries executed after the final evidence gain;
- source-admission precision before and after marginal-value filtering;
- outcomes split by challenge type, case, repetition, and policy position.

The final report also includes an exploratory stop-judgment reliability measure:
how often an interim `all gaps closed` decision disagrees with the separate blind
final gap assessment. This diagnostic was added after such a disagreement was
observed during execution. It does not change a replacement gate or rescue a
policy that fails the frozen decision rules; it tests whether the stopping
trigger can be trusted and identifies cases requiring adjudication.

The gate rejection audit is explicitly limited. A rejected query that was never
executed has no observed result and cannot be labeled a false rejection from
model opinion alone. Such cases remain unknown unless another arm issued a
materially equivalent query or a later bounded verification executes it.

## Competing explanations

Evaluate every result against all six hypotheses in the research brief. The
analysis should prefer elimination over confirmation:

- If unconstrained adaptation improves closure, H0 weakens.
- If gap binding improves precision or reduces repeated intent, H1 weakens.
- If proposal gating improves useful-query yield, weak proposals contributed to
  the earlier failures.
- If marginal-value admission improves precision without losing closure, H2
  gains support.
- If deterministic stopping reduces work after the final gain, H3 gains
  support.
- If improvements occur only in one challenge type or disappear when a case is
  removed, H4 gains support and any adoption must be narrow.
- If the conclusion changes under equal weights, ambiguous-as-open treatment,
  unavailable-source removal, manual adjudication, or leave-one-case-out tests,
  H5 gains support.

Evidence may support more than one explanation. Record inconsistent and
non-diagnostic evidence rather than forcing a winner.

## Decision form

The final recommendation must name one of these outcomes:

1. keep fixed retrieval;
2. adopt a bounded recovery policy for named gap types only;
3. adopt the full bounded policy for the tested domain;
4. run a new experiment because a specific uncertainty is decision-critical.

Any adoption recommendation must state the trigger, proposal requirements,
admission rule, stop rule, hard budgets, observed benefit, observed harm, and
the cases to which the evidence does not generalize. A future experiment must
name the uncertainty it resolves and the result that would change the decision.
Record the final choice in the versioned `w10-policy-selection` artifact. It binds
the primary summary, adjudication sensitivity, accounting dossier, challenge
cases, and ADR-0081. A decision-changing adjudication sensitivity can select only
the new-experiment outcome and must not silently authorize W11 measurement.

## Research-methodology completion check

Before reporting the study as complete:

- account for every query, candidate, exclusion, acquired source, failure, and
  adjudication in the durable evidence package;
- preserve source-to-claim links separately from the synthesis;
- complete the competing-hypothesis matrix and identify any linchpin result;
- reproduce numeric findings from the retained records and report variation
  across all three repetitions;
- distinguish observed results, counterfactual comparisons, analyst inference,
  and unanswered questions;
- run a pre-mortem on the proposed decision and state what evidence would
  reverse it.

## SOURCES

- [W10 research brief](w10-research-brief.md)
- [W10 frozen protocol](w10-frozen-protocol.md)
- [W10 research log](w10-research-log.md)
- `research-methodology/SKILL.md`, especially the academic/comprehensive,
  technical-verification, structured-analysis, and synthesis guidance
