# W11 SlopSearX substrate analysis plan

Status: **declared before scored Family A measurement**

This plan defines how the W11 evidence will be interpreted. Family A asks a
narrow question: when GroktoCrawl supplies the same completed W10 query plan,
does SlopSearX recorded continuation preserve research quality while improving
the durability and auditability of retrieval? The other W11 families test
different contracts and remain separate hard gates or capability findings.

## Primary comparison

The primary unit is one of the 12 W10 challenge cases. Each case is run three
times through both arms. Measurements are first averaged within a case; the
three repetitions are not treated as 36 independent research questions.

The primary outcome is the paired change from flat HTTP retrieval to recorded
continuation in importance-weighted claim closure. The co-primary guardrail is
the paired change in admitted-source precision. A case bootstrap with 10,000
samples and the frozen work-order seed produces 95% intervals.

Recorded continuation is non-inferior only when:

- the lower confidence bound for claim-closure change is at least -0.02; and
- the lower confidence bound for admitted-source-precision change is at least
  -0.05.

Passing these bounds establishes research-quality non-inferiority. It does not
establish that the added substrate is useful enough to adopt.

## Integrity and hard gates

The primary analysis runs only on a complete paired record. Every arm must use
the same case, query sequence, query order, engine list, result limit,
acquisition rules, grader prompt, model route, and case deadline. The retrieval
summary separately verifies pair completeness, query and engine equality,
SlopSearX budget accounting, and recorded caller completion.

HTTP compatibility, grant isolation, provenance, recovery, composition, and
operator viability are hard gates. A failure in one of these dimensions cannot
be offset by a higher research-quality score. Failed or missing trials remain
failures; they are never converted into zero-cost successes or silently
discarded.

## Secondary measures

Report result-set overlap as a transport diagnostic, not as a quality score.
Also report useful-query yield, unnecessary queries, eligible-source diversity,
acquisition success, elapsed time, model calls, search attempts, engine
attempts, admitted results, stored bytes, and trajectory completeness. Report
medians and distributions as well as aggregate values.

## Sensitivity analyses

Run the frozen primary analysis first. Label the following as sensitivity or
exploratory analyses:

- split results by execution order and repetition;
- retain failures, then separately show the effect of excluding infrastructure
  failures or unavailable sources;
- remove one case at a time to expose a result driven by one case;
- compare model grades with the seeded adjudication sample and all required
  disagreement cases;
- report result-set overlap beside quality deltas to distinguish transport
  variation from grading variation; and
- inspect challenge types separately without treating the small strata as
  confirmatory tests.

## Competing explanations

Interpret the result against these alternatives:

- **H0:** recorded continuation preserves quality and adds useful durable
  accounting;
- **H1:** any difference comes from result ordering or timing despite matched
  plans and engine scope;
- **H2:** persistence changes what can be retrieved or admitted, weakening
  source precision or claim closure;
- **H3:** the transport is sound but operational cost, failure recovery, or
  ownership makes broad adoption unattractive;
- **H4:** value exists only for a bounded contract such as receipts,
  continuation, saved-search events, or dependency dossiers.

Record evidence that supports and weakens each explanation. Do not force a
single explanation when the experiment is non-diagnostic.

## Decision and pre-mortem

The final recommendation is one of adopt, narrow adoption, reject, or further
study under the rules in the frozen protocol. Before recommending adoption,
assume the decision failed six months later and test the most plausible causes:
hidden compatibility drift, grant leakage, lost or duplicated work after a
restart, misleading provenance, excess operator burden, and quality loss in a
case outside this sample. State which retained evidence addresses each risk and
what evidence would reverse the recommendation.

## SOURCES

- [W11 frozen-protocol candidate](w11-protocol.md)
- [W11 research brief](w11-research-brief.md)
- [W10 analysis plan](../adaptive-policy/w10-analysis-plan.md)
- `research-methodology/SKILL.md`, especially the academic/comprehensive,
  competing-hypothesis, uncertainty, and pre-mortem guidance

