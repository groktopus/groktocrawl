# W11 research brief: SlopSearX as a research retrieval substrate

Status: **complete — narrow adoption; see W11 findings and ADR-0082**
Decision owner: Magnus
Execution owner: GroktoCrawl experimental research workstream
Tracking issue: [#318](https://github.com/magnus919/groktocrawl-x/issues/318)

## Decision

Decide whether GroktoCrawl should use SlopSearX 0.5's opt-in MCP research
contracts for bounded retrieval work, while GroktoCrawl remains responsible for
the user's question, evidence-gap judgments, source acquisition, claim
verification, synthesis, and publication.

This is an architectural selection for the experimental fork. It is not a
claim that the design should replace GroktoCrawl mainline.

## Why this needs an experiment

W8 found that adaptive queries can recover evidence when a known gap exists,
but can also reduce source precision. W10 is measuring which GroktoCrawl-owned
policy, if any, controls that tradeoff reliably. SlopSearX 0.5 now offers a
different division of work: the caller still decides what is missing, while
SlopSearX can account for searches, preserve immutable result snapshots,
execute tightly bounded continuations, and carry discovery provenance into a
downstream retrieval receipt.

The new contracts could remove duplicated coordination code and make research
more reproducible. They could also create two planners, add an MCP dependency,
or make an apparently simple search path harder to operate. Feature presence
alone cannot answer that question.

## Research questions

1. Does caller-directed continuation preserve or improve claim closure and
   source precision under the same cumulative retrieval budget as the W10
   control?
2. Does staged search avoid unnecessary fallback work without hiding partial
   failures or weak first-stage results?
3. Can every acquired passage be traced back through a SlopSearX snapshot and
   result identity, with failures and exclusions represented honestly?
4. Does entity projection reduce repeated acquisition work while retaining
   conflicting source observations and without overstating corroboration?
5. Can interrupted workflows resume without losing evidence or charging the
   same work twice?
6. Does the combined system reduce implementation and operating complexity in
   practice?

Saved-search monitoring and dependency dossiers answer different questions.
They receive separate capability evaluations and cannot raise the score of the
general one-shot research comparison.

## Competing explanations

- **H1 — useful substrate:** SlopSearX's durable accounting and provenance
  improve evidence quality or reliability without material cost or complexity.
- **H2 — accounting only:** research quality is unchanged, but provenance,
  recovery, and operator clarity improve enough to justify the integration.
- **H3 — misplaced ownership:** the new workflow duplicates GroktoCrawl's
  decisions, adds failure modes, or weakens user-centered control.
- **H4 — conditional value:** continuation, staged search, entity grouping, or
  receipts are useful in narrower combinations, but the complete workflow is
  not.
- **H0 — insufficient evidence:** observed differences are too small, unstable,
  or confounded to support an architectural change.

## Fixed ownership boundary

| Responsibility | Owner |
|---|---|
| User question, constraints, and success standard | GroktoCrawl |
| Evidence-gap assessment and whether to continue | GroktoCrawl |
| Query execution, engine policy, immutable result snapshots, and attempt accounting | SlopSearX |
| Page retrieval, redirect/DNS safety, extraction, hashing, and passage capture | GroktoCrawl |
| Claim verification, contradiction handling, synthesis, and publication | GroktoCrawl |

SlopSearX state such as `plan_executed`, URL novelty, entity grouping, ranking,
or a receipt is operational evidence. None of it certifies that a research
question is answered, that sources are independent, or that a claim is true.

## Durable evidence package

The final W11 package must contain the frozen protocol and environment,
versioned cases, work order, complete public records, private acquisition
records, SlopSearX attempts and snapshots, retrieval receipts and manifests,
fault-injection records, exclusions, analysis code, grader packet,
adjudication, research log, decision report, and ADR. Every retained source and
every rejected source must be accounted for before the decision is published.

## Current prerequisite

W10 must finish and identify the control policy before W11 measurement begins.
Protocol and harness development may proceed, but no W11 outcome data may be
collected before the W10-dependent control, budgets, prompts, and case set are
frozen.
