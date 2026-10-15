# Add Independent Semantic Verification Before Claim Publication

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-19
- Scope: W12.3 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#323](https://github.com/magnus919/groktocrawl-x/issues/323)
- Supersedes: none

## Context and Problem Statement

GroktoCrawl X can prove that a claim has citations, provenance, and a structurally
valid publication path. Those checks cannot prove that the cited text entails the
claim, remains current, represents an independent source, or belongs to the same
subject. A semantic verifier could catch those errors, but it could also add
latency, refuse answerable claims, repeat the generator's mistakes, or accidentally
become a publication authority.

W12.3 compared the existing structural publication decision with one blinded
claim-level verifier across 12 frozen cases and three repetitions. The verifier saw
only the claim, exact evidence spans, risk, and evidence obligation. It did not see
the control decision, reference judgment, generator rationale, or desired answer.

## Decision Drivers

- Reduce critical false acceptance and missed contradictions.
- Preserve answerable claims without indiscriminate abstention.
- Bind every judgment to exact inputs and source spans.
- Keep publication authority outside the model verdict.
- Bound latency, calls, failures, and route drift.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Retain structural checks only | Lowest latency and cost | Fluent semantic errors can remain publication-eligible |
| Verify only high-risk claims | Limits cost and delay | Requires reliable risk routing and leaves low-risk errors unchecked |
| Verify every source-bound publication candidate | Consistent semantic gate and simpler routing | Adds one model call and requires live calibration and fail-closed operation |

## Proposed Decision

Add one independent semantic verification stage before a source-bound claim becomes
publication-eligible. The stage returns `supported`, `contradicted`, `insufficient`,
or `indeterminate`, exact cited and contradictory span identities, confidence, and
a non-authoritative publication recommendation.

Publish only when the verdict is `supported`, confidence is at least 70, the input
digest and verifier identity match caller-established values, and the existing
publication policy independently accepts the record. A verifier failure or invalid
response fails closed. The verifier must not receive generator rationale, reference
labels, arm identity, or hidden facts.

Start behind the experimental feature boundary. Retain receipts and monitor live
false rejection, latency, production case mix, and alias mapping before promoting
the stage into the stable API path.

## Evidence

The [W12.3 evidence packet](../../../experiments/evidence/claim-verification/w12.3-final/00-index.md)
validated 36 completed trials with no terminal failures. The verifier eliminated
all critical false accepts, reduced missed contradictions from two to zero in every
repetition, preserved all supported claims, and ignored the hostile source
instruction. Mean incremental calls were 1.0, mean latency was 11.9 seconds, and
high-risk p95 latency was 22.3 seconds.

The synthetic corpus concentrates failure cases and does not estimate production
frequency. This decision adopts the bounded contract and an experimental rollout,
not an unobserved production default.

## Consequences

- Claim publication gains a distinct semantic assessment record after structural
  source binding.
- The publication gate remains the authority; verifier output cannot publish or
  claim human approval by itself.
- Invalid, timed-out, or untraceable verdicts remain durable and fail closed.
- The verifier adds one model call and measurable latency to each enabled claim.
- Live calibration and rollback are required before stable-path promotion.
- This proposal becomes accepted only with maintainer approval.

## W9 pre-approval disposition — 2026-09-30

The [final W9 recommendation](../../../experiments/w9-replacement-readiness-decision.md)
supports this record at its bounded experimental scope after the completed
three-checkpoint pilot. At that pre-approval checkpoint, status remained proposed
pending Magnus Hedemark’s ratification; successful implementation evidence does not supply owner approval.

## Owner ratification — 2026-09-30

Magnus Hedemark approved the [bounded W9 recommendations](../../../experiments/w9-replacement-readiness-decision.md)
in this project conversation: “I approve the bounded ADR recommendations. Please proceed.”
This accepts the documented experimental scope only; the earlier pending note
is retained as decision history. It does not authorize production migration,
upstream replacement, or expansion beyond this record’s confirmation gates.
