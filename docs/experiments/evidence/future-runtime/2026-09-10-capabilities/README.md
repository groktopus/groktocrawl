# Pinned model and search capability substitution — 2026-09-10

This packet measures the final future-runtime scenario. A run begins under one
pinned model/search set, a newer set with a specialist stage becomes the default,
the old run resumes unchanged, and new work uses the upgrade through the same
`research/1` artifact contract.

All **60 retained runs** conformed. Old work used capability 1 exactly once, new
work used capability 2 and its specialist exactly once, and both returned the same
public schema. Tests also prove rollback for new admissions and quarantine missing
or incompatible pinned state before any model call.

| Runtime | Resume old + run new median | Control checkpoint |
|---|---:|---:|
| Imperative | 0.013 ms | 321 bytes |
| LangGraph | 2.492 ms | 40,960 bytes |

LangGraph resumed the existing checkpoint after the registry default changed. The
framework did not reinterpret state because application validation pinned policy,
state schema, receipt format, model, and search identities. The imperative reference
achieved the same behavior with a much smaller serialized state.

The useful LangGraph property is continuity across an evolving graph. Retained work
can finish on admitted semantics while new threads use new nodes and adapters. The
application still needs a compatible worker registry, quarantine path, rollout and
rollback selection, and stable client contracts.

Machine-readable evidence:

- [summary.json](summary.json)
- [measurements.jsonl](measurements.jsonl)
