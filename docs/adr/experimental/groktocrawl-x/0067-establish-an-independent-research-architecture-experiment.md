# Establish an Independent Research Architecture Experiment

- Status: accepted
- Deciders: Magnus Hedemark (fork maintainer)
- Date: 2026-09-04
- Scope: `magnus919/groktocrawl-x` only
- Supersedes: none yet; replacement decisions are separate ADRs

- Accepted: 2026-09-05 by Magnus Hedemark, following the W1 review packet.
- Acceptance scope: foundation contracts for a bounded fixture-backed prototype;
  implementation/evidence gates remain. No storage/runtime/recovery selection or
  external-provider spending is authorized by this acceptance.
- Acceptance record: [issue #15](https://github.com/magnus919/groktocrawl-x/issues/15).

## Context and Problem Statement

[Upstream discussion #427](https://github.com/groktopus/groktocrawl/discussions/427)
proposes explicit research orchestration, evidence-driven branching, and a separate
durable execution layer. Its follow-up separates operational `ResearchState` from
a portable Knowledge IR of claims, evidence, contradictions, derivations,
provenance, freshness, and verification. The Artifact Pyramid renders that knowledge
as summary, analysis, and dossiers.

On 2026-09-04 Magnus requested a new experimental fork and a delivery plan, then
clarified that the proposal can overturn inherited architecture decisions and
requires new ADRs. The authorized scope is establishing and planning this experiment;
it is not approval of every pending technical choice or a mainline migration.

The starting code is upstream main commit
`34b4975bc7baaf25510ed34029b957f95b59de70`. It already has a shared research event
engine, planning and gap-directed acquisition, one final synthesis (ADR-0064),
progressive discovery (ADR-0065), and optional independent session steps (ADR-0066).
Those are the experimental baseline, not evidence that the proposed architecture
has already shipped.

GitHub returned the existing `magnus919/groktocrawl` fork when a second fork was
requested. Magnus selected a separate public repository preserving upstream Git
history, leaving the existing fork unchanged. This repository is therefore a code
fork with recorded provenance, not a second GitHub fork-network entry.

## Decision Drivers

- Freedom to replace the research architecture without representing this as the
  successor to, or a required migration for, mainline GroktoCrawl.
- A reusable and inspectable research product independent of rendered prose and
  the selected orchestration framework.
- Explicit decisions about crash recovery, artifact retention, verification,
  streaming, cost, and deployment rather than accidental inherited constraints.
- Retain MIT attribution, review discipline, meaningful tests, and operator-facing
  honesty about implemented guarantees.
- Make continued investment, redesign, or abandonment evidence-based.

## Considered Options

1. **Rework mainline directly.** Avoids a divergent repository, but couples a large
   experiment to upstream users and release decisions. Does not meet the requested
   separation.
2. **Experiment inside the inherited architecture only.** Smaller initial changes,
   but prevents evaluating the actual proposal when accepted ADRs are treated as
   permanent constraints. Rejected as the governing approach.
3. **Create an independent experimental code fork with new ADRs.** Preserves history
   and engineering standards while allowing a new target architecture. Recommended.
4. **Start a new implementation without history.** Clean foundation, but loses
   useful provenance, tests, and the deterministic data plane. Not recommended.

## Decision Outcome

Propose option 3. Treat this repository as GroktoCrawl X, explicitly experimental
and not a replacement for [mainline](https://github.com/groktopus/groktocrawl).

Keep deterministic retrieval and crawling as tool/service boundaries. Decide their
physical deployment and interfaces through the new architecture work rather than
assuming every current service, datastore, or in-process integration must survive.
Use a specialized research workflow; a generic persona swarm is not the target.

Write separate MADR records for the execution/knowledge/rendering boundaries,
Knowledge IR contract, storage and retention, orchestration runtime, durable job
ownership, client protocols, and evaluation/adoption criteria. Every record lists
which inherited decisions it retains, extends, or supersedes, with exact scope.
ADR-0047's deferral and Valkey-native target are eligible for replacement; Temporal
is a genuine option, not a prohibited deviation. Technology selection follows
explicit requirements and comparative evidence.

Keep accepted ADR bodies intact. A proposed successor does not change the old
status. On acceptance, update the predecessor's status/successor links and this
fork's index; for partial supersession, state exactly which behavior remains in
force. Number from the next available number above the current maximum (0067 is
the first new record here; do not reuse the inherited 0058 gap). Reconcile future
upstream numbering collisions in an import PR. Do not move ADR files into a new
folder layout or edit upstream records.

Keep Conventional Commits, DCO sign-off, PR review, code-quality/runtime gates,
API/CLI parity, asynchronous webhook contracts, and relevant integration tests.
Changing an architectural contract requires updating its checks, not suppressing
them. Existing CI YAML is inherited source, not proof that fork runners, rulesets,
credentials, or deployment permissions are configured.

Follow the [implementation plan](../../../experiments/research-architecture.md). The
plan is a decision and delivery backlog; it does not claim new architecture is
implemented. Any upstream contribution, release, or replacement proposal is a
separate maintainer decision.

## Consequences

### Positive Consequences

- Architectural exploration can be substantial without redirecting mainline.
- Claims and evidence can be tested independently of the runtime and prose.
- Reviewers can distinguish existing behavior, proposed choices, and verified
  experimental behavior.
- Git history, attribution, and reusable engineering assets are preserved.

### Negative Consequences

- Separate CI, publishing identity, maintenance, and upstream reconciliation are
  required. Mainline artifacts and credentials cannot be assumed to serve the fork.
- Multiple storage and runtime candidates increase evaluation work; the plan caps
  experiments and records stop decisions.
- This code fork has no native second fork-network relationship on GitHub.
- No new durability, evidence verification, or compatibility guarantee exists until
  its implementation and acceptance checks ship.

## Confirmation

At charter review, the maintainer checks repository identity and upstream SHA,
README scope, the ADR index, and the plan's decision/verification traceability.
At each architecture PR, its owner supplies predecessor/successor scope, runnable
confirmation checks, observed evidence, and operator/API/CLI impacts. Missing
execution evidence is reported as unverified. The evaluation workstream owns the
metric definitions and evidence manifest; the adopting ADR links its result.
Revisit this charter if the experiment's scope changes or a mainline adoption is
proposed. This record remains proposed until reviewed and accepted in the fork.

## Links

- [Experiment plan and decision backlog](../../../experiments/research-architecture.md)
- [Contribution standards](../../../../CONTRIBUTING.md)
- [ADR index](README.md)
- [ADR-0024: Artifact Pyramid](../../0024-artifact-pyramid-cli-output.md)
- [ADR-0040: Sessions](../../0040-session-protocol.md)
- [ADR-0047: Current durability decision](../../0047-defer-restart-safe-execution.md)
- [ADR-0056: Twin evidence and calibration](../../0056-twin-ci-evidence-and-calibration.md)
- [ADR-0064: One final synthesis](../../0064-one-final-research-synthesis.md)
- [ADR-0066: Independent session steps](../../0066-opt-in-to-independent-session-steps.md)
