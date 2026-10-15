# Compare Research Runtimes Under One Policy

- Status: accepted for bounded experimental use
- Deciders: Magnus Hedemark
- Accepted: 2026-09-30; direct owner approval of the bounded W9 recommendations
- Date: 2026-09-04
- Scope: experimental research D4 / W4; `magnus919/groktocrawl-x` only
- Plan: issue [#11](https://github.com/magnus919/groktocrawl-x/issues/11)
- Supersedes: none; accepts the imperative default with optional advanced LangGraph use

## Context and Problem Statement

The proposed Knowledge IR changes research policy as well as its data model. A
comparison against the inherited loop cannot tell us whether a graph adds value.
ADR-0070 therefore separates incumbent policy (A), evidence-first imperative (B)
and equivalent graph (C). D4 needs a bounded implementation comparison, not a
framework choice inferred from a better answer demo.

## Decision Drivers

- Portable evidence, verification, rendering and client contracts.
- Identical policy, operation budgets and failure schedules across runtime arms.
- Inspectable branching, cancellation and state evolution without hidden retries.
- Demonstrable engineering value sufficient to justify dependency/operating cost.
- Recovery requirements independent of graph checkpoint technology.

## Considered Options

| Option | Benefit | Cost / boundary |
|---|---|---|
| Keep only the incumbent loop | No new framework | Cannot test whether explicit state improves the new policy's implementation |
| Typed imperative controller | Simple reference execution and explicit ownership | Branching, joins and recovery adapters require application code |
| LangGraph adapter over shared policy | Candidate for explicit graph state and branching | Reducer/checkpoint semantics and dependencies require conformance work |
| Pydantic Graph or another typed graph | Alternative representation worth retaining in the backlog | A third runtime increases comparison scope before a need is established |

LangGraph documents separate checkpoint and cross-thread storage facilities;
Pydantic documents a typed graph facility. These establish available abstractions,
not a performance or recovery verdict for this repository.
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[Pydantic Graph](https://pydantic.dev/docs/ai/graph/graph/)

## Decision Outcome

The complete comparison establishes **B, the typed imperative controller, as the
reference for the bounded current workflow** and retains **C,
LangGraph, as an optional advanced research runtime**. Do not make LangGraph the
sole controller or add it to the default production dependency set. Dynamic
specialist fan-out, durable human interrupts, historical-checkpoint forks, and
version-pinned continuation demonstrated material future value. Do not add a third
runtime without an explicit ADR-0070 protocol revision. Keep inherited behavior as
arm A.

Both adapters invoke the same policy functions and operation interfaces. Runtime
code owns scheduling only; it cannot modify prompts, source ranking, verification
eligibility, stopping policy or rendering. Pin dependency versions in the comparison
manifest. No managed graph service or additional deployment is implied by testing
the open-source runtime. Measure checkpoint overhead separately from scheduling so
B without persistence is not unfairly compared to C with synchronous persistence.

### State and operation contract

Use ADR-0068's compact `ResearchState`, with references to retained data rather than
evidence bodies. Each logical operation has a stable ID and input digest; attempts
have distinct attempt IDs. Persist decisions and nondeterministic results before
replaying dependent work when recovery is enabled. An operation result is immutable;
the same ID with different input or output is rejected rather than merged.

Concurrent branches return typed deltas keyed by operation ID. A single controller
applies a deterministic merge: deduplicate identical receipts, reject conflicts,
order presentation by reserved identity rather than finish time, and join only the
committed dependencies of the next decision. Never use last-writer-wins for budgets,
coverage or terminal outcomes. Reducers must not call tools or mutate the evidence
store. Application events follow ADR-0072 rather than exposing node names.

Reserve global and per-run source, time, token and estimated-cost capacity before
dispatch, including retries. A planner cannot increase its limits. Unknown external
usage stays conservatively charged until reconciled; failed attempts do not vanish
from accounting. One retry owner per operation prevents SDK, node and outer-loop
retries multiplying each other. Cancellation reaches admission waits and all active
children; late outputs cannot publish after cancellation wins. Preserve existing
provider admission controls through shared adapters and test nested concurrency.

Version state, policy, tools, model configuration and serialization separately.
Reject unsupported state versions; do not feed old checkpoints to changed reducers
and hope they work. D5 owns version-pinned recovery and migration. Framework tracing
is diagnostic data, not the Knowledge IR or a public event protocol.

### Comparison and decision rule

Use ADR-0070's deterministic cases, negative controls and at least 30 paired
repetitions per workload, with cold/warm measurements separated. Compare canonical
entities, recorded logical operations, budgets, artifact manifests and client
outcomes, allowing incidental ID/timestamp mappings. Do not require identical
internal scheduling. Include an adversarial completion order and repeated receipt.

The [execution confirmation matrix](../../../experiments/research-execution-confirmation.md)
adds runtime-specific evidence. Measure state/checkpoint size, scheduler latency,
p50/p95 end-to-end overhead, cancellation delay, dependency footprint and operational
steps. Freeze allowable regression bounds from the W1 baseline before candidate
runs. Demonstrate a concrete branch/join or recovery-policy change in both arms;
record the diff and reviewer assessment instead of inventing productivity scores.

Adopt a graph only when all applicable hard gates and predeclared bounds pass and
its demonstrated engineering benefit justifies the cost. Otherwise retain the
imperative reference, revise within the approved protocol, or report inconclusive.
A successful graph checkpoint test does not establish ADR-0074 recovery conformance.
No provider spend is authorized; scripted local fixtures are the initial lane.

Two paired fixture series now cover 30 repetitions of three workloads in cold and
warm lanes with different fixed seeds. All 720 retained records conformed. LangGraph
added measurable construction and scheduling overhead and did not replace the
application-owned policy, budget, receipt, cancellation, recovery, or publication
logic in this deliberately bounded comparison. That evidence supports the imperative
controller for today's workflow, but the comparison disabled persistence and did not
exercise adaptive replanning, specialist subgraphs, human interrupts, checkpoint
recovery, or alternate-path forks. Issue #245 must test those capabilities before a
long-term substrate decision. Numerical W1 quality bounds remain a separate
policy/model evaluation gate and are not needed to infer a quality difference between
runtimes that intentionally used identical scripted operations.

## Inherited Decision Impact

| ADR | Proposed relationship and scope |
|---|---|
| 0022, 0064, 0065 | Retain inherited streams; experimental adapter uses ADR-0072 publication/progress contract |
| 0031 | Retain centralized configuration; add versioned runtime settings only with implementation |
| 0033, 0051 | Extend source/admission/cancellation controls to all experimental branches and retries |
| 0035 | Retain graceful shutdown; it is not recovery proof |
| 0040, 0063, 0066 | Retain inherited session concurrency; runtime state does not take over session ownership |
| 0048 | Extend telemetry with runtime/policy version and operation identity |

No predecessor is superseded. Acceptance extends the bounded experimental runtime
choice: the typed imperative controller remains default, and advanced LangGraph
workflows remain optional under the shared policy, ownership and publication contracts.

## Consequences

The experiment can attribute runtime benefits without rewriting policy twice.
Shared interfaces and two adapters cost engineering time; deterministic fixtures
still cannot establish real-model quality. Keeping only two runtime arms bounds
that cost. The optional adapter preserves a tested path toward more adaptive
research without binding client contracts or artifact authority to LangGraph. A
negative or inconclusive result is useful and must be retained.

## Confirmation

Magnus ratified the bounded recommendation on 2026-09-30. The
W4 implementer owns CI conformance cases, with zero budget, identity, publication
or cancellation violations. Missing cases/results block adoption. Archive commit,
fixture/dependency versions, raw distributions, failed controls and exclusions in
the W4 report. Run contracts on every adapter/policy change; rerun measured comparisons
when their inputs change. Exceptions require a named approver, reason and expiry;
never waive hard publication/access invariants. Review after a reducer, runtime or
state migration change; retire with a successor ADR and preserved evidence. The
next review must include issue #245's future-capability evidence and judge user
value, extensibility, and recovery complexity rather than scheduler latency alone.

## Links

- [Experiment plan](../../../experiments/research-architecture.md)
- [ADR-0068](0068-separate-research-execution-knowledge-and-rendering.md)
- [ADR-0070](0070-evaluate-research-policy-and-runtime-separately.md)
- [ADR-0072](0072-expose-verified-research-through-an-experimental-protocol.md)
- [ADR-0074](0074-define-research-recovery-before-selecting-infrastructure.md)
- [W4 runtime comparison report](../../../experiments/runtime-comparison/report.md)
- [First paired measurement](../../../experiments/evidence/runtime-comparison/2026-09-08/)
- [Repeated paired measurement](../../../experiments/evidence/runtime-comparison/2026-09-09/)

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
