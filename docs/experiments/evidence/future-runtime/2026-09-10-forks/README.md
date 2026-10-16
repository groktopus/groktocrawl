# Competing-hypothesis checkpoint forks — 2026-09-10

This packet measures scenario 4 from the
[future runtime specification](../../../future-runtime-scenarios.md): branch an
investigation from retained evidence, change one hypothesis, and preserve both
results without repeating compatible evidence collection.

All **60 retained runs** conformed. Each parent and child shared one evidence call,
performed separate analysis calls, retained distinct results, and preserved the
parent checkpoint. Tests reject foreign-scope, changed-policy/model/search, deleted,
cancelled, and duplicate-child forks. An existing child remains independently usable
after its parent is deleted.

| Runtime | Fork median | Control checkpoints | Application journal |
|---|---:|---:|---:|
| Imperative | 0.494 ms | 495 bytes | 1,047 bytes |
| LangGraph | 1.084 ms | 32,768 bytes | 1,047 bytes |

The imperative arm explicitly reads a retained parent checkpoint, writes a child
checkpoint, and continues it. The LangGraph arm retrieves a historical state,
creates a new checkpoint from it with `update_state`, and executes that branch. The
original terminal checkpoint remains addressable and unchanged.

LangGraph makes time travel and alternate-path continuation concise and inspectable.
Its SQLite file is much larger because it retains database pages and full state
history. The application still owns ancestry, compatibility fingerprints, receipt
eligibility, scope, deletion, and independent parent/child identity. Native history
does not decide whether evidence may legally or semantically be reused.

These local fixture timings exclude models, tools, Valkey, and PostgreSQL. No
production package or service is added; pinned LangGraph and SQLite persistence stay
in the optional hosted W4 lane.

Machine-readable evidence:

- [summary.json](summary.json)
- [measurements.jsonl](measurements.jsonl)
