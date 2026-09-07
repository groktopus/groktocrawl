# Session storage probe — 2026-09-04

Run `SESSION_STORE_TEST_URL=redis://127.0.0.1:16379/0 PYTHONPATH=agent-svc:. python benchmarks/session_storage.py` against an isolated Valkey instance. Twenty samples per case on local Valkey 9.1; p95 is nearest-rank. CI additionally exercises Valkey 8 contracts. These are storage-operation timings, not end-to-end research session latency.

| Refs | Client round trips | p50 ms | p95 ms |
|---|---|---|---|
| 1 | 2 | 0.216 | 0.238 |
| 20 | 2 | 0.342 | 0.378 |
| 100 | 2 | 1.014 | 1.04 |

The old per-ref path needed eight client commands per reference. Bulk writes use a TTL metadata read plus one atomic Lua commit, independent of ref count. Payload bytes still grow with the new reference data.

| Existing artifact chars | New content bytes | p50 append ms | p95 append ms |
|---|---|---|---|
| 0 | 10 | 0.266 | 0.384 |
| 100000 | 10 | 0.24 | 0.338 |
| 1000000 | 10 | 0.241 | 0.353 |

Each append transfers only the new Unicode section plus fixed command/key metadata. It does not transfer the existing artifact; metadata reads use the character counter. History writes likewise append only the new step. Legacy artifact length initialization reads existing bytes server-side once.

Deterministic tests verify that a delayed Redis call does not block the event-loop heartbeat, eight-operation admission stays bounded, cancellation drains an executing native write, and real Valkey preserves 20 concurrent appends, legacy JSON empty arrays/objects, custom TTL, deletion and late-write rejection.
