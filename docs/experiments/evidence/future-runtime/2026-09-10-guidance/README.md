# Durable human guidance comparison — 2026-09-10

This packet measures scenario 3 from the
[future runtime specification](../../../future-runtime-scenarios.md): complete
research, pause with a question, lose the process, and continue from the person's
answer without repeating completed work.

## Result

All **60 retained runs** conformed: 30 imperative and 30 real LangGraph runs. Every
run used a new fencing generation after restart, made one research call, made one
synthesis call, and produced the same guided result. The tests also cover process
loss before the pause and after guidance acceptance, incompatible state, conflicting
guidance, cancellation, deletion, expiry, and foreign-scope access.

| Runtime | Pause median | Resume median | Control checkpoint | Application journal |
|---|---:|---:|---:|---:|
| Imperative | 0.465 ms | 0.222 ms | 173 bytes | 719 bytes |
| LangGraph | 2.274 ms | 1.363 ms | 28,672 bytes | 719 bytes |

These are local controller timings with deterministic callbacks. They exclude model,
network, Valkey, and PostgreSQL time. The SQLite file size includes database page
overhead, so it describes this fixture's operating footprint rather than the size of
one logical state object.

## What LangGraph contributed

This is LangGraph's strongest result so far. Its native interrupt records the exact
place where execution stopped. Reopening the same checkpointer and thread continues
after that boundary without custom program-counter dispatch. The checkpoint after
the research node means process restart does not repeat that completed call. A
checkpoint after accepted guidance can continue directly into synthesis.

The framework did not make recovery safe by itself. Before every resume, the
application still validates scope, state version, and the guidance receipt, then
reacquires the W5 Valkey lease and fencing generation. The application journal owns
research, guidance, and synthesis receipts; the graph contains only control state
and receipt IDs. Cancelled, deleted, expired, or foreign-scope work is rejected by
the application authority before the graph executes.

The imperative arm achieves the same user behavior with a much smaller checkpoint
and no framework persistence dependency. It also requires explicit checkpoint-file
handling and manual pause/resume dispatch. LangGraph trades a larger checkpoint and
two optional packages for a clearer, extensible continuation model.

No production dependency or service is added by this experiment. The hosted fast
lane runs the real pinned SQLite checkpointer; the hosted storage lane separately
proves the same coordinator against the real Valkey W5 owner.

Machine-readable evidence:

- [summary.json](summary.json)
- [measurements.jsonl](measurements.jsonl)
