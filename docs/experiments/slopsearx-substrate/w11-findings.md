# W11 findings: SlopSearX as a research substrate

Status: **complete — narrow adoption**

## Decision

Keep ordinary SlopSearX HTTP search as GroktoCrawl X's default retrieval path.
Do not adopt the complete recorded-continuation workflow in its current form.
Retain the separately proven snapshot, receipt, manifest, recovery,
saved-search, and dependency-dossier contracts as opt-in building blocks for
future bounded features.

This decision applies only to the experimental fork. It does not authorize a
mainline replacement or production cutover.

## What the comparison established

The final retrieval run contained 72 complete trials: 12 challenge cases,
three repetitions, and two matched transports. Every one of the 36 pairs used
the same query, seven-engine scope, and eight-result limit. Every pair returned
the same result URLs in the same measured set, for a result-set Jaccard score
of 1.0. Recorded continuation added a median 26.731 ms in this local deployment.

The complete workflow failed its ownership gate in all 36 recorded runs.
SlopSearX marked each job `succeeded` with
`stop_reason: result_budget_exhausted` as soon as the exact eight-result budget
filled. GroktoCrawl's later completion update was acknowledged, but the durable
record retained `caller_completed: false`. The transport preserved retrieval,
yet its terminal state said the workflow was complete before the research owner
made that decision.

The post-run HTTP compatibility suite passed. Grant isolation, engine-scope
equivalence, deterministic contracts, snapshot provenance, receipt replay,
interruption recovery, saved-search lifecycle, dependency dossiers, and the
composition path also passed their bounded tests. Entity projection preserved
information but did not reduce acquisitions in the live cases.

## Why model grading does not decide W11

The two transports returned identical result sets, so a stable evaluator should
receive equivalent evidence. More importantly, the frozen protocol says an
ownership or workflow-accounting failure blocks adoption regardless of average
quality.

The post-gate model diagnostic was abandoned after 11 of 72 trials. Five grades
completed; six failed through provider timeouts, null content, or missing
required schema fields. Sixty-one were deliberately not attempted. This packet
is too incomplete to support a quality estimate, and continuing would have
spent model calls without changing the hard-gate decision.

## What remains useful

- Immutable result snapshots and retrieval identities improve reproducibility.
- Idempotent receipts and manifests preserve the discovery-to-capture chain.
- Recovery tests showed charged attempts, stale-owner fencing, retained
  snapshots, and replay without duplicate search or receipt creation.
- Saved searches provide a credible opt-in monitoring primitive.
- Dependency dossiers organize partial evidence conservatively and expose
  missing coverage.

These contracts should stay behind adapters and explicit grants. They do not
become proof that a source is true, independent, applicable, or sufficient.

## Conditions for reconsidering recorded continuation

Reopen the general workflow comparison only after SlopSearX represents caller
completion independently from execution-budget exhaustion. A replacement run
must preserve the exact result cap, show `caller_completed: true` in every
recorded pair, retain ordinary HTTP compatibility, and use a calibrated reviewer
that passes a small strict-schema screen before full grading.

## Competing explanations

- **Useful substrate:** supported for bounded durability, provenance, and
  recovery contracts; not supported for the complete workflow.
- **Accounting only:** strongly supported. Retrieval was identical while the
  durable record added useful detail.
- **Misplaced ownership:** supported for terminal workflow state because budget
  exhaustion preempted the caller's completion decision.
- **Conditional value:** best fit for the evidence. Several narrow contracts
  passed while broad continuation failed.
- **Insufficient evidence:** applies to model-scored quality, but does not erase
  the deterministic ownership failure or identical retrieval sets.

## Pre-mortem

If narrow adoption fails later, the most likely causes are grant drift, hidden
HTTP compatibility regressions, retained state outliving its intended policy,
receipt linkage being mistaken for truth, or operators enabling the full MCP
surface to obtain one small capability. Named least-privilege profiles,
before/after compatibility tests, explicit retention, typed provenance, and
adapter-level contract tests are the required controls. Any future design that
needs broad grants or exposes SlopSearX job state as GroktoCrawl's public model
should return to architecture review.

## Evidence

- [Execution packet](../evidence/slopsearx-substrate/2026-09-19-general-comparison/README.md)
- [Retrieval summary](../evidence/slopsearx-substrate/2026-09-19-general-comparison/retrieval-summary.json)
- [Diagnostic accounting](../evidence/slopsearx-substrate/2026-09-19-general-comparison/diagnostic-grading-accounting.json)
- [Operator assessment](w11-operator-assessment.md)
- [Research log](w11-research-log.md)
- [ADR-0082](../../adr/experimental/groktocrawl-x/0082-delegate-bounded-retrieval-to-slopsearx.md)
- [GroktoCrawl X #327](https://github.com/magnus919/groktocrawl-x/issues/327)
- [SlopSearX #387](https://github.com/magnus919/SlopSearX/issues/387)
