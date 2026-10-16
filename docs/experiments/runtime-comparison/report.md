# Runtime comparison status

The W4 conformance seam is implemented in
`agent.experimental.runtime_comparison`. The imperative reference and the
optional `LangGraphRuntime` invoke the same `OperationSpec`, `Budget`,
`ExecutionLedger` and `ScriptResult` contracts. The adapter builds one
LangGraph node per operation, uses graph edges for dependencies, and serializes
ledger commits when independent branches finish concurrently. LangGraph is an
optional comparison dependency; it is not part of the production image and no
framework persistence or recovery is enabled. `GraphCandidateRuntime` remains
as a framework-neutral deterministic test double.

The unit suite covers equivalent accounting, reverse branch completion with a
stable join, cancellation with unsettled reservations, cyclic-graph rejection
and over-budget receipt failure. These are conformance controls, not quality or
performance evidence.

## Optional framework smoke evidence

On 2026-09-07, an isolated `uv run --with langgraph==0.6.11` environment ran
the W4 unit suite with **10 passed and 0 skipped**. The real LangGraph adapter
matched the imperative reference for a parallel branch/join case after
canonical operation-ID mapping, and cancellation preserved the unsettled
reservation without publishing a late output. The run used scripted local
callbacks, no provider spend, and no LangGraph checkpoint or store. It is a
framework conformance smoke result, not a paired performance measurement,
recovery proof, quality claim, or adoption decision.

The [manifest](manifest.json) records the optional LangGraph dependency
specifier and the exact version used by the smoke run. After W1 packet approval,
the first paired fixture measurement was recorded in
[`docs/experiments/evidence/runtime-comparison/2026-09-08/`](../evidence/runtime-comparison/2026-09-08/):
30 paired repetitions across three workloads in each first-run and repeated-state
lane, with zero conformance failures. LangGraph added measurable scheduling
overhead in this harness, including one retained first-run outlier, while both
adapters produced equivalent accounting and outputs.

The measurement is not a quality result, checkpoint/recovery proof, or production
selection. A second repeated measurement is recorded in
[`docs/experiments/evidence/runtime-comparison/2026-09-09/`](../evidence/runtime-comparison/2026-09-09/).
It uses a new fixed seed and repeats the same 30-per-workload cold/warm design;
all 360 additional records again conformed. The fresh-process cold lane shows
LangGraph graph-construction overhead (p50 181–194 ms versus 0.8–1.7 ms for the
imperative reference); the warm lane shows p50 3.3–5.1 ms versus 0.7–1.5 ms.
These are fixture observations, not end-to-end product bounds. `baseline_frozen`
remains false because the W1 numerical quality and resource bounds are still
unresolved.

## Engineering assessment

The bounded-workflow recommendation is to retain the typed imperative runtime as
the reference and keep LangGraph as an optional candidate. Across two seeds, both runtimes produced
equivalent terminal state, outputs, budgets, and accounting in 720 retained run
records with zero conformance failures. LangGraph therefore proved it can express
the current workflow, but it did not improve a measured product or engineering
outcome in this comparison.

The candidate added graph construction and scheduling overhead in every workload.
Its second-run fresh-process p50 was 181–194 ms, compared with 0.8–1.7 ms for the
imperative runtime. Its warm p50 was 3.3–5.1 ms, compared with 0.7–1.5 ms. Those
small absolute costs would be hidden by real model and network time, so latency is
not the deciding objection. The deciding point is that the adapter still relies on
the same application-owned budget ledger, receipts, cancellation rules, durable
ownership, and publication gates. It adds a framework and another state model
without removing the hard parts of this architecture.

This comparison did not exercise the features most likely to justify a graph
substrate: adaptive replanning, specialist subgraphs, human interrupts, checkpoint
recovery, or forks from prior state. Issue #245 adds those future-facing scenarios.
The long-term decision remains open until that evidence shows whether LangGraph
lets the platform safely build materially better research experiences as models and
search tools improve. Pydantic Graph is not added as a third arm because ADR-0073
bounds this study to one framework candidate until that need is established.

## Future-facing scenarios 1 and 2

The first two future-capability implementations and their retained
[measurement packet](../evidence/future-runtime/2026-09-09/) strengthen the case
for continuing the LangGraph study without changing the current recommendation.
Across 420 adaptive-replanning and dynamic-specialist records, the imperative and
LangGraph arms had zero conformance failures.

Adaptive replanning benefits from visible conditional routing, but the implementation
advantage is modest: the application must still own evidence judgments, receipts,
budgets, time limits, and terminal publication. Dynamic specialist fan-out is a more
natural fit. LangGraph's `Send` mechanism accepts a question-dependent number of
branches, and its reducer makes concurrent aggregation explicit. New specialist
assignments did not require a graph or public-contract change.

That flexibility adds framework-specific state and reducer failure modes. The real
LangGraph controllers are larger in these compact fixtures and take roughly 1.2–1.9
ms at the median versus 0.06–0.37 ms for the imperative versions. Network and model
work will dominate that absolute difference. The more important unresolved test is
whether native interrupts, checkpoints, restart, and forks simplify ambitious user
journeys without competing with the W5 execution ledger or PostgreSQL authority.

## Future-facing scenario 3

The [durable human-guidance packet](../evidence/future-runtime/2026-09-10-guidance/)
records 60 conforming pause/restart/resume runs. LangGraph's native interrupt and
thread checkpoint materially simplify continuation from an exact control boundary.
Completed research was reused after a new process opened the same checkpoint, and a
checkpoint after guidance acceptance continued directly into synthesis.

This benefit depends on a strict split of responsibility. The W5 Valkey ledger still
owns claims, fencing, expiry, cancellation, and terminal state. The application
journal owns guidance and operation receipts. LangGraph stores control position and
receipt references. It cannot authorize resume or establish that an external effect
is safe to repeat.

The local LangGraph fixture used a 28,672-byte SQLite checkpoint file versus a
173-byte imperative control file, and its median pause/resume operations were about
2.27/1.36 ms versus 0.46/0.22 ms. Those costs are acceptable for an optional research
runtime, but persistence retention, migration, and cleanup remain operating work.
Scenario 3 supplies a concrete future-platform advantage. Fork isolation and
capability-version substitution still need evidence before ADR-0073 can decide the
long-term substrate.

## Future-facing scenario 4

The [checkpoint-fork packet](../evidence/future-runtime/2026-09-10-forks/)
records 60 conforming parent/child comparisons. LangGraph's retained history and
`update_state` API create an alternate continuation from a precise checkpoint while
leaving the parent's terminal checkpoint unchanged. This is a second concrete
future-platform advantage after durable interrupts.

The imperative reference performs the same operation by explicitly loading and
cloning a 495-byte control checkpoint. Its median fork took 0.49 ms versus 1.08 ms
for LangGraph, whose SQLite history occupied 32,768 bytes. The larger footprint buys
built-in history traversal and branch construction, but requires retention and
cleanup policy.

Application authority remains decisive. It records parent/child ancestry and rejects
reuse when scope, policy, model, search identity, deletion, or cancellation differs.
Both arms made one shared evidence call and distinct analysis calls. Model and search
capability substitution is now the final planned scenario before ADR-0073 review.

## Future-facing scenario 5 and recommendation

The [capability-substitution packet](../evidence/future-runtime/2026-09-10-capabilities/)
records 60 conforming old-run/new-run pairs. Old checkpoints retained their pinned
model, search, policy, state, and receipt semantics after a new capability became
the default. New work used the new search and specialist while both returned the
same `research/1` artifact shape. Incompatible workers quarantined state before a
model call.

Retain the typed imperative controller as the simple reference and retain LangGraph
as the optional advanced research runtime. LangGraph adds larger checkpoints,
dependencies, retention work, reducer rules, and version-support duties. It also
materially improved dynamic specialist fan-out, durable human interrupts, and
alternate continuations from historical checkpoints.

Do not let LangGraph checkpoints own evidence, budgets, authorization, or artifacts.
Promote it beyond the optional experimental path only after a real research pilot
demonstrates user value and PostgreSQL-backed checkpoint retention, migration,
backup, and cleanup pass their own operating gates.
