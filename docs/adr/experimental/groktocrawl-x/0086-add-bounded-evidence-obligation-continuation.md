# Add Bounded Evidence-Obligation Continuation

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-19
- Scope: W12.4 experiment in `magnus919/groktocrawl-x`
- Plan: issue [#324](https://github.com/magnus919/groktocrawl-x/issues/324)
- Supersedes: none

## Context and Problem Statement

W10 found that generic adaptive querying did not reliably outperform fixed
retrieval. The missing control surface may be an explicit account of what evidence
is still required. W12.4 isolates that question: should a bounded experimental
path continue only for a named, open evidence obligation with a declared closure
rule and measurable evidence gain?

W12.1 separately rejected universal model-authored mission normalization. This
decision therefore cannot require every request to produce an obligation ledger.

## Decision Drivers

- Continue for a concrete missing requirement rather than general uncertainty.
- Preserve exact candidate, admission, closure, and stopping provenance.
- Stop after closure, zero gain, or a shared hard budget.
- Avoid recurring cost on easy questions.
- Keep fixed retrieval available while live behavior remains uncalibrated.

## Considered Options

| Option | Benefits | Costs and risks |
|---|---|---|
| Fixed retrieval only | Predictable and cheap | Cannot recover a known missing primary, freshness, identity, or contradiction obligation |
| Generic adaptive continuation | Can broaden retrieval | May repeat intent and spend work without a named evidence delta |
| Obligation-driven continuation | Auditable reason to continue and deterministic stop | Depends on accurate obligation authoring and live retrieval yield |

## Proposed Decision

Add evidence-obligation continuation as an experimental option. An obligation has
an identity, kind, importance, closure rule, and required evidence identities. A
continuation proposal must bind to one open obligation. Acquired evidence closes
the obligation only after admission under the source identity, derivation,
materiality, and closure rules.

Execute at most three total queries in the initial bounded profile. Stop when all
obligations close, the next obligation has no registered proposal, a continuation
produces no material gain, or the budget is reached. Preserve fixed retrieval as
the stable default. Obligation profiles must be caller-supplied or independently
validated during the experimental phase; they are not mandatory universal intake.

## Evidence

The [W12.4 packet](../../../experiments/evidence/evidence-obligations/w12.4-final/00-index.md)
contains 108 deterministic replay records. The treatment passed every gate in all
three repetitions, closed 90% of challenge weight, stopped both easy cases without
continuation, and spent 4.5% of queries after the last material gain. Its one open
obligation was intentionally unanswerable.

The constructed corpus tests causal policy mechanics, not live prevalence or
automatic obligation quality. It supports the experimental contract, not stable
default promotion or the observed 90-point effect as a production forecast.

## Consequences

- Experimental clients may opt into a typed, auditable continuation denominator.
- Fixed retrieval remains the stable behavior.
- Zero-gain work terminates the path instead of inviting another model proposal.
- Automatic obligation authoring and live retrieval require a separate calibration.
- The ledger records control state and does not become a knowledge store or truth
  authority.
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
