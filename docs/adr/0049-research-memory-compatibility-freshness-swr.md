# Research Memory Compatibility Fingerprint, Freshness, and Stale-While-Revalidate

- Status: accepted
- Deciders: GroktoCrawl maintainers
- Date: 2026-08-15

- Research-substrate scope: Existing cache behavior remains unchanged. The opt-in research-substrate scope follows [ADR-0069](0069-adopt-bounded-research-substrate.md); the former experiment decision records are preserved in the [provenance archive](experimental/groktocrawl-x/README.md).

## Context

Research memory (ADR-0041) can substitute a complete synthesized answer for the live search, scrape, and LLM pipeline. Replay eligibility originally depended on semantic prompt similarity plus an optional user scope. That does not prove compatibility with response-affecting request constraints such as explicit URLs, output schema, model choice, research depth, image collection, citation style, or URL-constraint strictness.

Freshness also had correctness gaps: `max_age_hours` was accepted by the query API but not applied; freshness was derived from the process TTL rather than the artifact's stored expiry contract; malformed creation timestamps were treated as current; the worker read an `age_hours` field the query never returned; and Qdrant references could outlive missing Valkey artifacts unless the manual sweep endpoint was invoked.

Issue #529 hardens the replay contract before any wider stale-cache work (#532), and adds an opt-in stale-while-revalidate (SWR) mode so callers can trade a short staleness window for lower tail latency.

## Decision

We introduce three coordinated mechanisms, all inside `agent-svc`:

### 1. Compatibility fingerprint

A canonical SHA-256 fingerprint is computed from every request field that changes source selection, synthesis shape, or response semantics: normalized `prompt`, sorted `urls`, canonicalized `schema_`/`output_schema`, `model`, `search_type`, `include_images`, `citation_style`, `strict_constrain_to_urls`, and `force_fresh`. Dispatch/accounting fields (`mode`, `stream`, `webhook`, `max_credits`) are excluded. `citation_style` participates because artifacts are stored post-transform: replaying under a different style would re-transform already-transformed text.

The fingerprint is stored on the Valkey entry and the Qdrant payload at admission, and compared at replay. A missing or mismatched stored fingerprint is a miss (fail-closed). The raw `/v2/research-memory/query` debug endpoint does not supply a fingerprint and therefore does not enforce compatibility; agent replay always supplies one.

### 2. Freshness from stored timestamps, fail-closed

`age_hours` and `freshness` derive from the artifact's stored `created_at`/`expires_at`, never from the process TTL. Freshness boundaries are `TTL/4` (`fresh`) and `TTL/2` (`aging`), with the TTL read from `expires_at - created_at`. Malformed, missing, zero/negative-TTL, or already-expired timestamps are a miss — never a `datetime.now(UTC)` fallback. A caller-supplied `max_age_hours` is applied as a hard age gate, and the query returns accurate `age_hours`, `expires_at`, and a `compatibility` decision.

### 3. Opt-in stale-while-revalidate

`AgentRequest` gains `stale_while_revalidate` (default `False`) and `max_stale_hours` (default small). A `stale` hit within `max_stale_hours` past the `TTL/2` boundary, with SWR enabled, is returned immediately labeled `freshness="stale"` and `refreshed=False`, and one background refresh is started keyed by the fingerprint. Single-flight uses an in-process `asyncio.Task` registry (a dict on `ResearchMemory`); no queue, table, or persistent service is added. The refreshed result is exposed via a `refreshed` SSE event (streaming) or by overwriting the completed job's data key so the existing `GET /v2/agent/{id}` handle reflects it (non-streaming). Outside the window, with `force_fresh`, or with SWR off, blocking-fresh behavior is unchanged. Responses distinguish `fresh`, `aging`, `stale`, and `refreshed`.

### 4. Automatic dual-store sweep

A tracked background task sweeps dangling Qdrant references (Valkey artifact missing) on a fixed interval, respecting the existing `TaskTracker` graceful-shutdown contract. The manual `/v2/memory/sweep` endpoint remains. Sweep runs and orphan counts are emitted as counters/gauges (`groktocrawl_research_memory_sweep_runs_total`, `groktocrawl_research_memory_orphans_swept_total`, `groktocrawl_research_memory_orphans`).

## Consequences

- Positive: replay cannot substitute an answer across incompatible request constraints, and freshness/metadata are accurate and observable.
- Positive: SWR is opt-in, single-flight, and reuses the existing job/stream handles rather than adding a public refresh endpoint.
- Negative: legacy entries without a fingerprint are no longer eligible for agent replay until re-admitted; this is a one-time cache-warming cost and is the fail-closed trade-off.
- Negative: `force_fresh=True` results are fingerprinted with `force_fresh=True` and therefore never replayed, which can accumulate entries that are only reachable via the raw memory endpoint; this follows the field list specified in the issue.
- Scope guardrail: this change deliberately does not rework `crawl_cache.py` or add a general scrape-cache stale window; cache-assisted retrieval is deferred to #532.

## Links

- [ADR-0041 Research Memory — Cross-Session Semantic Cache](0041-research-memory.md)
- [ADR-0035 Graceful Shutdown for Fire-and-Forget Tasks](0035-graceful-shutdown.md)
- [ADR-0048 Stage-Level Latency and Capacity Telemetry](0048-stage-level-telemetry.md)
