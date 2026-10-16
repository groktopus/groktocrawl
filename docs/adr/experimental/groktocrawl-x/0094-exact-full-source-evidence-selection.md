# Exact full-source evidence selection and scoped recovery

* Status: proposed; implementation prepared for review
* Deciders: Magnus
* Date: 2026-10-07

## Context

Inherited synthesis prefixes exclude late facts even when full content is retained.
Experimental passage construction rejects more than 120 KB or 32 passages; that
bound must not turn into silently truncated or supposedly exhaustive evidence.
ADR-0092 removed query acquisition maxima and remains unchanged.

## Decision

Extend existing source artifacts, session authority and exact SourcePassage records.
Keep complete permitted source text independent from model context. Select lexical,
verbatim Unicode spans over the full supplied bodies under an aggregate selected
character budget (default 32000, explicit range 256–128000 per synthesis call).
Each body receives a fair share; unused shares carry forward. Headers and metadata
are additional bounded-by-source-count context overhead, not selected characters.
No additional acquisitions, embeddings, GPU workload or provider calls are required.

Report source identity, content-bound snapshot identity, whole-content digest, exact
span offsets and quote digests, selected/omitted totals and recovery instructions.
Coverage describes supplied text only and never asserts semantic/exhaustive answer
coverage. Lexical selection can miss paraphrases and split tables: callers can narrow
sources, raise the explicit budget, or request digest-pinned contiguous pages.

Expose an owned-session evidence reader through API, CLI and MCP. Fail closed for
foreign/deleted/expired refs, content changes and ref deletion during scanning.
New sessions derive scope from existing request credentials; legacy unowned opaque
session IDs remain compatible. No private evidence enters a shared new index.

Construction-bound errors are classified and point to query passage preparation;
that helper operates only on already-authorized retained snapshots. It does not
activate rejected ADR-0080 orchestration. Retention admission remains upstream.
Cache fingerprints include selection version and budget. Cache and SSE preserve
exact selection metadata. Source data is explicitly treated as untrusted context.
CPU scans run off the event loop with cooperative cancellation.

## Consequences

Late numeric and contradictory facts become available without discarding originals.
Deterministic identities support citation inspection and paging. Selection improves
access but is not entailment validation. Concurrent pages can change, hence digest
preconditions. Multiple independent summary/highlight calls have separate per-call
budgets and expose their actual per-transformation coverage.

## Validation

Offline fixtures cover Unicode spans, late tables, contradictions, many sources,
low/large budgets, retention preservation, owned/deleted refs, digest conflicts,
construction-limit recovery, cancellation and cache/stream metadata. Existing fast,
CLI/MCP/surface/type and independent Runtime CI gates remain required before merge.
No deployment is authorized by this record.
