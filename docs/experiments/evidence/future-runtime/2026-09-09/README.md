# Future runtime scenarios 1–2 — 2026-09-09

This packet measures adaptive replanning and dynamic specialist fan-out from the
[future runtime specification](../../../future-runtime-scenarios.md). Both the
typed imperative controller and real `langgraph==0.6.11` use the same application
ledger, budgets, deterministic adapters, outcomes, and synthesis rules.

## Result

All **420 retained records** conformed. Neither runtime lost evidence, changed a
terminal outcome, exceeded an operation limit, or produced a different synthesis.
The functional tests also prove receipt reuse, contradiction preservation, bounded
replanning, cancellation before publication, stable synthesis under reversed
completion order, and one-, three-, and five-specialist fan-out.

| Scenario | Workload | Imperative median | LangGraph median |
|---|---|---:|---:|
| Adaptive | adequate first pass | 0.058 ms | 1.218 ms |
| Adaptive | weak then adequate | 0.100 ms | 1.491 ms |
| Adaptive | contradiction then recovery | 0.143 ms | 1.762 ms |
| Adaptive | replan limit | 0.143 ms | 1.761 ms |
| Specialists | one branch | 0.128 ms | 1.465 ms |
| Specialists | three branches | 0.228 ms | 1.613 ms |
| Specialists | five branches | 0.368 ms | 1.895 ms |

These are warmed, local controller-fixture timings. The timer includes graph
construction on every run and excludes network, model, and storage latency. The
absolute difference is too small to decide product fitness.

The retained application state is byte-for-byte the same size in both arms for
every workload because LangGraph state is not an authority and is not persisted in
these scenarios. Checkpoint size and recovery behavior belong to scenario 3.

## Engineering finding

Conditional edges make the adaptive loop and its stop conditions visible as a
workflow. The imperative version remains slightly smaller: 62 controller lines
versus 76 for LangGraph. The graph improves the shape of future extension, but the
application still owns receipts, limits, evidence, and publication safety.

Dynamic `Send` fan-out is the stronger result. The number of specialists becomes
runtime data and the reducer makes parallel aggregation explicit. Adding legal and
operations specialists required new assignments, with no change to the graph or
public result. The cost is more framework-specific state plumbing and a larger
controller: 77 lines versus 33 for the imperative version. Reducer definitions are
a new correctness surface, so stable-order and cancellation tests remain required.

No new service, database, or production dependency was added. LangGraph remains a
pinned, optional CI dependency. This packet supports continuing the experiment; it
does not yet support choosing LangGraph as the default runtime.

Machine-readable evidence:

- [summary.json](summary.json)
- [measurements.jsonl](measurements.jsonl)
