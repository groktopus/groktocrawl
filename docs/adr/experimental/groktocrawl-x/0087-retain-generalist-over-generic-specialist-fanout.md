# Retain the Generalist Over Generic Specialist Fan-Out

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-19
- Scope: W12.5 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#325](https://github.com/magnus919/groktocrawl-x/issues/325)
- Supersedes: none

## Context and Problem Statement

Specialists can isolate context and search distinct evidence obligations, but each
branch creates handoff, duplication, conflict, cost, and reconciliation risks.
Prior work proved that imperative and LangGraph runtimes can execute dynamic
branches consistently. W12.5 asks the separate product question: does a bounded
specialist pattern materially improve the final research artifact at a matched
resource ceiling?

## Decision Drivers

- Improve evidence coverage and final usefulness on separable complex tasks.
- Preserve source identity, exact evidence, contradictions, and visible failures.
- Avoid delegating simple or non-separable work.
- Keep total cost within a declared bound.
- Require reliable independent evaluation before adoption.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Generalist default | Lowest coordination cost and one clear synthesis owner | May miss separable evidence roles |
| Generic bounded fan-out | Broadens coverage mechanically | Handoff loss, false consensus, evaluation complexity, and added cost |
| Narrow task-specific specialists | Can target a proven recurring need | Requires a calibrated router and task-class evidence not established here |

## Proposed Decision

Retain one generalist as the default research owner and reject generic specialist
fan-out. Do not route work to multiple investigators merely because a task is
complex or because the runtime supports branching.

Keep `specialist-evidence-handoff/1` as an experimental contract. Any future
specialist trial must pre-register a narrow separable task class, preserve exact
source and evidence identity, remain unable to publish directly, expose failures
and conflicts to the lead, use a matched resource ceiling, and pass a calibrated
independent evaluation.

This decision does not choose between the imperative runtime and LangGraph. The
runtime comparison remains separate under ADR-0073.

## Evidence

The [W12.5 packet](../../../experiments/evidence/specialist-value/w12.5-final/00-index.md)
contains 33 completed and three terminal failed paired reviews. Treatment improved
structural coverage by 66.7 percentage points and preserved contradictions, but
blinded usefulness gains were 1.1, 1.2, and 9.1 points, below the 10-point gate in
all repetitions. Three malformed responses remained after bounded retries, and
the reviewer used the 0–100 scale inconsistently. No repetition passed the full
gate.

## Consequences

- The default architecture keeps one research owner and one publication boundary.
- Runtime support for fan-out remains available for future experiments, without
  implying product adoption.
- Typed handoffs provide reusable provenance, authority, budget, and conflict
  controls for a narrower test.
- A future task-specific proposal must bring real-task evidence and calibrated
  evaluation rather than reusing this synthetic effect size.
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
