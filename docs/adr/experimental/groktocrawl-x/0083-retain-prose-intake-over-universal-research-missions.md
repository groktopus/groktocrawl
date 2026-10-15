# Retain Prose Intake Over Universal Research Missions

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-19
- Scope: W12.1 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#321](https://github.com/magnus919/groktocrawl-x/issues/321)
- Supersedes: none

## Context and Problem Statement

The replacement architecture needs an intentional representation of user goals,
constraints, evidence obligations, uncertainty, and stopping conditions. A full
typed Research Mission could make those concerns inspectable and durable, but it
could also add ceremony, distort ambiguous requests, or create a fragile model
normalization step before useful research begins.

W12.1 compared the incumbent equivalent prose brief with a typed Research
Mission across 12 frozen cases and three repetitions. The cases covered
straightforward, ambiguous, compound, temporal, contradictory-source, and
unanswerable work in the enterprise agentic-software-factory domain.

## Decision Drivers

- Improve obligation coverage on ambiguous and compound requests consistently.
- Reduce scope violations without losing user intent or hard boundaries.
- Avoid harming straightforward requests.
- Keep latency and model cost within the frozen operational limits.
- Preserve a path to richer future orchestration without forcing an unreliable
  normalization step on every request.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Require the full typed Research Mission for every request | Uniform state and explicit obligations | Poor intake accuracy, lost boundaries, correction burden, and inconsistent downstream benefit |
| Retain equivalent prose intake and discard mission concepts | Lowest immediate complexity | Loses useful typed concepts for internal state and future targeted workflows |
| Retain prose as the default and use smaller or explicitly triggered contracts | Preserves current reliability while allowing focused evolution | Requires future experiments to name a trigger and prove each narrower contract |

## Proposed Decision

Retain equivalent prose intake as the default. Do not place the tested
`research-mission/1` normalizer in front of every research request.

Preserve typed obligations, hard boundaries, evidence roles, clarification rules,
and budgets as design material for smaller internal contracts or explicitly
triggered advanced workflows. Any successor must define a narrower trigger,
avoid reconstructing a large mission object from underspecified prose, and pass
a new frozen comparison before adoption.

## Evidence

The [W12.1 evidence packet](../../../experiments/evidence/research-mission/w12.1-final/00-index.md)
validated without integrity issues. The typed treatment passed the difficult-case
effect gate in one of three repetitions and lost 12.5 percentage points of
coverage in the other two. Intake action accuracy was 40%, mean field recall was
2.9/100, and blind graders found 11 hard-boundary failures. Twenty independent
regrades did not produce a treatment advantage. Operational cost, latency, and
downstream hard-boundary gates passed.

## Consequences

- W12 does not add a universal pre-research normalization call.
- The current prose path remains the comparison baseline for later value
  experiments.
- Orchestrators may carry typed internal state after intent is sufficiently known;
  this ADR does not require that state to be LangGraph-specific.
- Future mission work must focus on narrower, user-visible value rather than on
  schema completeness alone.
- This proposal becomes accepted only with maintainer approval; the experiment's
  mechanical rejection does not substitute for decision authority.

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
