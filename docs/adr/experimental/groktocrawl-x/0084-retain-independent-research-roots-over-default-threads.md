# Retain Independent Research Roots Over Default Longitudinal Threads

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-19
- Scope: W12.2 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#322](https://github.com/magnus919/groktocrawl-x/issues/322)
- Supersedes: none

## Context and Problem Statement

GroktoCrawl X retains research roots, source snapshots, claims, and provenance as
independent artifacts. A longitudinal Research Thread could connect those records
so a follow-up investigation can say what changed, what remains true, and what is
still unresolved. It could also increase prompt load, blur temporal status, or
make accumulated context appear more authoritative than its sources.

W12.2 compared a fresh independent follow-up with the same follow-up supplied an
explicit append-only Research Thread. Both arms received identical initial and
follow-up source snapshots. Nine longitudinal cases covered changed facts,
renamed features, derivative sources, contradictions, historical support, later
resolution, link rotation, near-match subjects, and no change. Each case ran three
times under a frozen matched design.

## Decision Drivers

- Improve change and current-state accuracy consistently.
- Preserve historical truth without presenting it as current.
- Avoid false merges and authority inherited from thread membership.
- Reduce the work and latency required for a trustworthy update.
- Preserve inspectable source and claim lineage for future capabilities.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Supply an accumulated Research Thread to every follow-up | Gives the model explicit continuity and unresolved work | More context, slower answers, temporal leakage, and inconsistent quality |
| Keep independent roots and construct explicit comparisons | Preserves source authority and historical records; limits contamination | Requires deliberate comparison work for follow-ups |
| Adopt the thread only for named continuity workflows | Could preserve a future product path | Needs a narrower trigger and new evidence before adoption |

## Proposed Decision

Keep independently durable research roots as the default. For follow-up research,
construct an explicit comparison from the relevant roots and current sources. Do
not inject the tested `research-thread-experiment/1` representation into every
follow-up prompt, and do not treat thread membership, recency, or semantic
similarity as evidence of truth or identity.

Retain stable subject identifiers, source lineage, temporal claim status,
corrections, and unresolved obligations as narrow internal records. They remain
useful for audit, comparison, and future experiments without requiring a living
thread to own knowledge.

## Evidence

The [W12.2 evidence packet](../../../experiments/evidence/research-thread/w12.2-final/00-index.md)
validated all 54 follow-up trials and all 54 grade records. The treatment missed
the practical-effect gate in every repetition and averaged 2.8 points worse across
the 25 fully observed pairs. It was 23.0% slower, used 51.2% more model tokens, and
produced two stale-current leaks. Conservative scoring of two missing treatment
grades lowered the reported all-pair effect to -9.0 points but did not change the
decision. The no-change and operational gates passed.

## Consequences

- The replacement architecture keeps research roots and source snapshots
  independently durable.
- A follow-up may request an explicit, inspectable comparison; accumulated thread
  prose is not silently added to every prompt.
- Source lineage and historical claim status remain first-class records because
  they support audit and safer comparison without granting authority.
- A future user-invoked continuity view must define a narrow trigger, prevent
  stale-current leakage, and pass a new frozen comparison before adoption.
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
