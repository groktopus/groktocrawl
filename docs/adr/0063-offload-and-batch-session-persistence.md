# Bounded Nonblocking Session Persistence

- Status: accepted
- Deciders: GroktoCrawl maintainers
- Date: 2026-09-04

## Context

Synchronous Redis calls blocked the API event loop. A 20-reference step issued
160 commands just to store refs, while artifact appends and step history rewrote
the complete accumulated values. Moving calls into threads alone leaves an
unbounded work queue and lets cancellation release a session lock before a
native write completes.

## Decision

Use a transitional offload boundary of eight admitted operations per store.
Drain native work before propagating cancellation and releasing session locks.
Redis connection/read timeouts bound stalled calls. Lock-acquisition cancellation
compares and deletes its ownership token after the pending SET completes.

Use guarded Lua commits for bulk refs, artifact append, and step append. Each
checks session existence at commit time, refreshes all data-key TTLs together,
and updates expiry metadata. Artifact APPEND and a character counter avoid
transferring existing Markdown. Legacy sessions missing the counter initialize
it atomically by counting UTF-8 leading bytes before appending.

Keep existing `:steps` JSON as an immutable legacy prefix; append new raw JSON
entries to `:step_log`. Read both in one Lua snapshot and combine in Python.
Do not re-encode user JSON through Lua cjson, which can change empty arrays into
objects. Step indices are assigned atomically. Delete and TTL refresh include
the new log key. Public export and reference shapes stay unchanged.

This remains an in-process execution design. Each storage operation is atomic;
a whole research step's metadata, refs and artifact still spans operations under
the existing step lock. Full-step atomic commit and opt-in independent step
execution are separate work in #625.

## Consequences

Event-loop responsiveness no longer depends on synchronous storage latency.
Ref commits take two client round trips (TTL read and guarded bulk write),
independent of reference count. Artifact and history writes transfer only new
data. Full history/export reads still transfer the requested complete output.
Cancellation may wait for an already executing write to finish; this prevents
late writes after ownership is released. A failed step may have partial committed
state until the full-step commit protocol is added; deletion cannot resurrect it.

## Validation

Delayed Redis doubles verify heartbeat responsiveness and bounded admission;
cancellation tests verify native-write drain. A real Valkey CI contract tests
concurrent history and Unicode appends, legacy empty-array/object preservation,
custom TTLs, deletion and late-result rejection.

## Links

- [ADR-0040](0040-session-protocol.md)
- Issues #625 and #626
