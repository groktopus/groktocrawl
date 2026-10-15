# Evaluate Research Policy and Runtime Separately

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-04
- Scope: experimental research evaluation and adoption gates
- Plan: D7 / W1; issue [#3](https://github.com/magnus919/groktocrawl-x/issues/3)
- Supersedes: none; extends inherited evaluation discipline

- Accepted: 2026-09-05 by Magnus Hedemark, following the W1 review packet.
- Acceptance scope: foundation contracts for a bounded fixture-backed prototype;
  implementation/evidence gates remain. No storage/runtime/recovery selection or
  external-provider spending is authorized by this acceptance.
- Acceptance record: [issue #15](https://github.com/magnus919/groktocrawl-x/issues/15).

## Context and Problem Statement

A new graph runtime paired with a better evidence policy may outperform the current
pipeline, but that comparison cannot identify which change mattered. Conversely,
deterministic fixtures can verify contracts without measuring real-model judgment.
The experiment needs both forms of evidence, with a clear ceiling on each claim.

The inherited [answer eval harness](../../../../evals/README.md) already checks pinned
claim strings, citation mappings, abstention cases and provenance. It explicitly
does not establish general reasoning quality. Reuse its fixtures, fail-closed
manifests and reviewed baseline promotion instead of treating them as a semantic
verification oracle.

## Decision Drivers

- Attribute benefits to research policy or runtime without confounding the two.
- Detect unsupported claims, lost contradictions and unnecessary work.
- Measure latency, provider usage and operational overhead under comparable bounds.
- Preserve failed runs and uncertainty; prevent automatic baseline rewriting.
- Make adoption, revision or rejection an explicit decision with inspectable evidence.

## Considered Options

| Option | Advantages | Disadvantages |
|---|---|---|
| Compare final answers from old and new stacks | Cheap demonstration of perceived quality | Confounds prompts, sources, budgets, runtime and policy |
| Use only deterministic fixture scores | Reproducible and inexpensive | Does not test real-model entailment, source judgment or generalization |
| Use only an LLM judge | Scales to many outputs | Correlated errors, judge drift and opaque scoring can hide failures |
| Separate contract/runtime and policy-quality experiments | Attribution, reproducible contracts and independently reviewed quality | Additional corpus curation, reviewer effort and controlled trial cost |

## Decision Outcome

Propose the fourth option. Define three arms:

- **A — incumbent:** pinned starting research implementation and policy.
- **B — evidence-first imperative:** proposed Knowledge IR policy with a simple
  imperative controller and framework-independent contracts.
- **C — equivalent graph:** the same policy, tools, model adapters and contracts as
  B, executed through a graph runtime selected for the bounded comparison.

Compare **B vs C** for runtime effects, and **A vs B** for policy effects. A vs C
may be reported as an overall product comparison, but cannot attribute an effect
to the framework. Add a third runtime only through an explicit extension of the
experiment protocol. No full rewrite or paid run is authorized by accepting a
benchmark design alone.

### Experiment 1: contract and runtime conformance

Use deterministic source snapshots and scripted model responses for equivalent
logical operations. Pin policy, tools, operation identity, limits, schema and
normalization versions. A fixture must fail if the expected scripted operation is
not exercised; it cannot silently fall back to a generic successful response.

B and C must agree on canonical semantic entities and externally meaningful
outcomes. Ignore incidental generated IDs/timestamps and framework-internal traces;
compare stable entity mappings, claim support/qualification, budget use, terminal
state and published artifact revision. Do not require identical prose or internal
node order. Client event ordering requirements are defined by D6.

Cases cover direct support, contradictory evidence, insufficient evidence,
staleness, copied sources, ambiguous units/time, malformed model output, duplicate
operation completion, cancellation, budget exhaustion, interrupted commits, and
source instructions that attempt to change policy. At least 20% of cases exercise
negative or abstention behavior. Include failing controls (for example, deliberately
mis-cited claims) to prove each grader can reject an invalid result.

Hard gates on every declared case:

- No invalid/dangling committed reference within its retention contract, wrong
  citation identity, invalid derivation, or silent replacement of snapshot content.
- No budget overrun, unowned side effect, ignored cancellation, duplicate committed
  effect, or falsely reported completion under the tested failure schedule.
- No ordinary factual publication after failed/indeterminate required verification;
  qualified uncertainty remains a valid outcome.
- No unmarked provisional output or cross-revision artifact mixture.
- Existing relevant regressions pass, with approved contract changes explicitly
  reflected in both baseline and expectations.

Storage/recovery cases become required when D3/D5 implementations exist. Until
then, label them pending and do not claim durable-execution conformance. Missing
check results never count as passes.

Measure local runtime overhead with identical scripted responses and fixed delays,
recording cold and warm runs separately. Use at least 30 repetitions per workload,
randomized paired order, the same host limits, and p50/p95 plus distributions. W1
pins the allowed overhead before comparing candidates; the report includes both
absolute milliseconds and relative change. A threshold cannot be widened after
seeing results without a new reviewed experiment revision.

### Experiment 2: research-policy quality

Use the same frozen source corpus and retrieval candidates, provider/model settings,
and matched maximum time/token/source/cost limits across A and B. Separate any later
live-retrieval study: changing search results must not be mistaken for a policy gain.
Record actual usage, not just the ceiling. Treat errors, timeouts and empty outputs
as trial outcomes rather than silently dropping them.

The evaluation owner creates a development set and a held-out question set,
stratified by straightforward, ambiguous, contradictory, stale, source-dependent,
and unanswerable requests. Include multi-part questions. Keep questions, expected
subquestions, permissible claims and evidence reviewed before candidate runs.
Prevent prompt/verifier tuning on the held-out set; discovered corrections require
an explicit corpus revision and a fresh comparison.

A bounded pilot starts with at least 30 held-out questions and five trials per arm
per question where outputs are stochastic. This is exploratory evidence, not a
universal statistical-power guarantee. The evaluation owner performs a power or
uncertainty assessment before a strong adoption claim; widen the sample only within
approved spend. Report question-level paired uncertainty, not a false independent
sample of every generated claim or repeated trial.

Primary measures:

| Measure | Denominator / interpretation |
|---|---|
| Supported material assertions | Independently identified factual assertions in the output; report count and support rate |
| Required-question coverage | Fixed, pre-reviewed required subquestions/claims per task; omissions cannot improve the result invisibly |
| Citation correctness/coverage | Material assertions with required evidence links; a valid URL alone is not correctness |
| Conflict and abstention handling | Pre-reviewed conflict/unanswerable cases; distinguish appropriate uncertainty from refusal on answerable questions |
| Freshness and source independence | Cases with dated facts or known shared lineage; unknown metadata remains unknown |
| Work and latency | Per-question totals for calls, tokens, source attempts, estimated/actual cost, time to useful progress and verified answer, total p50/p95 |

Use a reviewed semantic rubric distinguishing support, partial support, contradiction,
unsupported assertion and legitimate inference. Reviewers see the question, source
context and anonymized output, not the candidate identity. A second reviewer
adjudicates disagreements and all high-severity unsupported claims. An LLM judge
may assist after calibration against human-reviewed examples; record judge/model/
prompt versions and disagreements. It is not the sole adoption authority.

If only one human reviewer is available, record that limitation and keep the
quality conclusion provisional rather than representing a second model as a second
human. Deterministic fixture authors cannot substitute expected strings for an
independent semantic review of real-model outputs.

### Preflight manifest and decision rule

Before each comparison series, check in a reviewed manifest containing:

- Arm commit/policy/runtime/schema/normalization versions and the baseline identity.
- Corpus IDs/hashes, split membership, expected checks, scenario/fixture versions.
- Provider/model settings, trial count, seed where supported, ordering and hardware.
- Per-operation/run/series source, token, time and cost ceilings, including retries,
  plus pricing assumptions and a stop mechanism. Paid runs require a separately
  approved budget; the default series budget is zero external provider spend.
- Primary metrics, task denominators, allowed latency/cost/quality regression bounds,
  minimum evidence and uncertainty rule, severity rubric and named reviewer.

Do not invent performance thresholds before W1's baseline. An incomplete manifest
blocks comparison, not ADR discussion or fixture authoring. Candidate baselines are
written separately and promoted only in a reviewed source-control change.

Adoption requires all applicable hard gates, no observed unresolved critical
unsupported claim or provenance failure, and satisfaction of the predeclared quality,
coverage and overhead bounds. A graph runtime also needs demonstrated engineering
value (for example, a concrete branch/recovery change that is easier to implement
and inspect) sufficient to justify dependency and deployment costs. Report that
assessment with an example and reviewer, not a fabricated numerical productivity score.

If uncertainty overlaps the declared decision boundary, record **inconclusive**;
continue only within approved bounds or revise/reject the candidate. Never retry
until a favorable score appears or remove difficult cases to achieve acceptance.
Adoption is a separate ADR; upstream contribution is a separate maintainer decision.

## Inherited Decision Impact

| Record / artifact | Relationship | Scope |
|---|---|---|
| ADR-0018, 0048 | Extend | Preserve stage/capacity observations; add policy/runtime/verification version context and verified-output timing |
| ADR-0054, 0055 | Retain and extend fixtures | Source-owned deterministic twins remain protocol fixtures, not proof of real-model research quality |
| ADR-0056 | Retain evidence discipline | Versioned manifests, bounded live work, sanitized evidence and no automatic fixture promotion |
| ADR-0057 | Retain | No recurring mutation-testing CI added by this proposal |
| Existing answer eval harness | Extend | Reuse graders and negative controls where appropriate; add IR/coverage tests without claiming current string checks prove entailment |

## Consequences

The experiment can distinguish a useful knowledge model from a useful framework.
Results expose failures and uncertainty rather than a single opaque score. Corpus
curation and independent semantic review take time and may limit adoption claims.
Frozen retrieval improves attribution but cannot establish live-web reliability;
that needs a separately bounded later study.

## Confirmation

The evaluation owner must show that a broken citation, hidden contradiction,
missing required subquestion and budget violation each fail their intended grader.
Magnus reviews the preflight manifest, threshold rationale and held-out separation
before comparative execution. Each series records sanitized provenance and all
outcomes, including failures. Raw evidence lives behind appropriate access controls;
public logs contain safe summaries and IDs/hashes, not arbitrary prompts or secrets.

The W7 decision links the exact runs, comparison report, reviewer findings, unresolved
risks and adoption/stop decision. Revisit the protocol if model, corpus, verifier,
policy, runtime, outcome definition or spending bounds change.

## Links

- [Execution boundaries](0068-separate-research-execution-knowledge-and-rendering.md)
- [Knowledge IR](0069-define-versioned-knowledge-and-verification.md)
- [Worked example](../../../experiments/research-foundation-example.md)
- [Experiment plan](../../../experiments/research-architecture.md)
- [Existing evaluation harness](../../../../evals/README.md)
