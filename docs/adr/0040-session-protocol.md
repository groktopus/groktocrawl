# Session Protocol for Agent-Native Research

* Status: proposed
* Deciders: magnus
* Date: 2026-07-03

Technical Story: AI agents conducting research through GroktoCrawl need stateful sessions
where intermediate results accumulate server-side — the agent directs the research without
carrying full page content in its context window.

- Research-substrate scope: Existing session operations remain unchanged. The opt-in research-substrate scope follows [ADR-0069](0069-adopt-bounded-research-substrate.md); the former experiment decision records are preserved in the [provenance archive](experimental/groktocrawl-x/README.md).

## Context and Problem Statement

Today's `POST /v2/agent` is a fire-and-forget call: the client submits a prompt, the server
searches, scrapes, and synthesizes, and returns a final answer. There is no way to:

1. **Steer research incrementally.** An agent may want to search broadly, browse a few results,
   then narrow the search based on what it finds. Today this requires the agent to carry full
   scraped content in its context across multiple stateless API calls.

2. **Reference past steps.** When an agent wants to drill into a specific source from step 2
   of a 5-step research flow, it must re-submit or manually track that source.

3. **Export accumulated research.** After completing a multi-step investigation, the agent
   needs a single artifact containing all sources and findings — not a collection of individual
   API responses to reassemble.

4. **Manage lifecycle.** Sessions should expire to free resources, support cancellation, and
   isolate concurrent research flows from each other.

The solution is a session protocol: a state machine backed by Valkey that accumulates an
artifact tree server-side, exposing compact step results to the client while retaining full
content for export.

## Decision Drivers

* Agents must steer research without accumulating full page content in context windows
* Results must reference prior steps by index (step 2, source 3)
* Sessions must support the existing actions (search, scrape, query) plus future actions
  (deepen, export, plan)
* Storage must be durable but bounded (Valkey TTL, configurable max session size)
* Must integrate with the existing `JobStore` Valkey patterns and `asyncio.create_task()`
  inline processing model
* Backward compatible: existing stateless endpoints remain unchanged
* Must work with the existing LLM client, scraper client, and search client without refactoring

## Considered Options

### Option A: Valkey-backed Session State Machine (Chosen)

A new `SessionManager` class that owns the full session lifecycle. Each session is a Valkey
hash with typed sub-keys (meta, steps, artifact tree, references). Steps are executed by the
existing research pipeline functions but results are accumulated into the session rather than
returned directly.

**Pros:**
- Clean separation from existing `JobStore` — sessions are a distinct domain concept
- Valkey is already available and proven in this codebase
- TTL-based expiry is built into Valkey, no custom sweep needed
- Atomic operations (HSET, HGET) support concurrent step execution
- Can reuse the `store.py` pattern (create/get/update/delete) that workers already understand

**Cons:**
- Valkey is an additional operational dependency (already present)
- Session data is lost on Valkey restart (acceptable — sessions are ephemeral)
- No built-in schema enforcement for artifact tree structure

### Option B: In-Process Session Store with Checkpointing

Keep sessions in Python memory with periodic Valkey snapshots for durability.

**Pros:**
- Faster read/write (no network round-trip per step)
- Can use Python type system directly for artifact tree

**Cons:**
- Sessions lost on process restart between checkpoints
- Doesn't scale to multiple API processes
- Requires custom TTL sweep implementation
- Adds complexity (checkpoint logic, dual storage)

### Option C: SQLite-backed Sessions

Use SQLite with WAL mode for session storage in the container filesystem.

**Pros:**
- No additional service dependency
- Schema enforcement via SQL

**Cons:**
- Introduces a new storage paradigm alongside Valkey
- No built-in TTL expiry (requires application-level sweep)
- Filesystem-bound — doesn't scale to multiple instances
- Workers would need to learn SQL patterns alongside existing Valkey patterns

## Decision Outcome

Chosen option: **Option A — Valkey-backed Session State Machine**

### Architecture

```
POST /v2/session/create        → SessionManager.create() → Valkey HSET
POST /v2/session/{id}/step     → SessionManager.step()   → Run action → Valkey HGET/HSET
GET  /v2/session/{id}          → SessionManager.get()    → Valkey HGETALL (compact view)
POST /v2/session/{id}/export   → SessionManager.export() → Build artifact → return markdown
DELETE /v2/session/{id}        → SessionManager.delete() → Valkey DEL
```

### Valkey Key Schema

```
session:{id}:meta     → JSON {id, status, created_at, expires_at, step_count, total_credits}
session:{id}:steps    → JSON [{index, action, query, result_summary, timestamp, credits_used}]
session:{id}:artifact → plain text markdown (accumulated, append-only)
session:{id}:refs     → JSON {ref_id: {url, title, markdown, scraped_at, depth}}
```

- **meta**: Lightweight session metadata, always returned to client
- **steps**: Ordered list of step summaries (compact — no full content, just result_count, url_count, etc.)
- **artifact**: Accumulated markdown document built as the session progresses. Each step appends
  a section with its findings and source references. This is what `export` returns.
- **refs**: Full content indexed by reference ID (`ref_0_2` = step 0, source 2). Agents request
  specific refs via citation resolution rather than receiving all content.

### SessionManager API

```python
class SessionManager:
    def __init__(self, redis_url: str, default_ttl: int = 3600)

    async def create(self) -> str
        # Create new session, return session_id

    async def step(self, session_id: str, action: str, params: dict) -> dict
        # Execute action (search, scrape, query, deepen, export)
        # Accumulate results into session artifact and refs
        # Return compact summary (not full content)

    async def get(self, session_id: str) -> dict | None
        # Return session metadata + step summaries (no full refs)

    async def export(self, session_id: str, format: str = "markdown") -> str
        # Return full accumulated artifact

    async def resolve_ref(self, session_id: str, ref_id: str) -> dict | None
        # Return full content for a specific reference

    async def delete(self, session_id: str) -> bool
        # Delete session and all keys

    async def extend_ttl(self, session_id: str) -> None
        # Reset TTL on session activity
```

### Step Actions (Initial Set)

| Action | Description | Params | Returns |
|--------|-------------|--------|---------|
| `search` | Search via SearXNG, store results as refs | query, limit, sources, categories | ref_count, top_urls (titles only, no content) |
| `scrape` | Scrape specific URLs, store content as refs | urls[] | ref_count, summaries |
| `query` | Run LLM over accumulated context | prompt | answer (compact), ref_count_cited |
| `deepen` | Targeted search on a specific ref | ref_id, sub_topic | new_ref_ids, inserted_section |

### Integration with Existing Pipeline

Session steps reuse the existing research pipeline functions:
- `search` action → `SearXNGClient.search()` → results stored as refs
- `scrape` action → `ScraperClient.scrape_with_fallback()` → markdown stored as refs
- `query` action → `LLMClient.generate()` with accumulated artifact as context
- `deepen` action → targeted search on sub-topic + scrape + LLM synthesis, results inserted
  at the referenced location in the artifact tree

The existing `_run_job_with_observability()` scaffolding is NOT used for session steps because
sessions are long-lived and steps are synchronous (the client blocks on each step). If a step
needs async processing (e.g., batch scrape), it returns a `step_id` for polling — but the
default is synchronous.

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v2/session/create` | Create a new research session. Returns `{session_id, expires_at}` |
| POST | `/v2/session/{session_id}/step` | Execute a step. Body: `{action, params}`. Returns step summary |
| GET | `/v2/session/{session_id}` | Get session status, step history (compact), artifact length |
| POST | `/v2/session/{session_id}/export` | Export accumulated artifact. Body: `{format: "markdown"}` |
| POST | `/v2/session/{session_id}/resolve` | Resolve ref IDs to full content. Body: `{ref_ids: ["ref_0_2"]}` |
| DELETE | `/v2/session/{session_id}` | Delete session and all associated data |

### Session TTL

- Default TTL: 1 hour (3600s), configurable via `SESSION_TTL` env var
- TTL refreshes on each `step()` call (idle timeout)
- Hard expiry at `SESSION_MAX_TTL` (default 24 hours) regardless of activity
- TTL applied to all session keys (`meta`, `steps`, `artifact`, `refs`)
- Expired sessions return 404 on access

### Concurrency & Isolation

- Sessions are isolated by `session_id` — no cross-session data access
- Concurrent steps within a session: the step number is determined atomically via Valkey `HINCRBY`
- If two steps race on the same session, the second waits for the first's step to complete
  before incrementing the counter (enforced via a per-session Valkey lock with 30s timeout)
- No cross-session locking — each session progresses independently

## Positive Consequences

* Agents can steer multi-step research without carrying full page content in context
* The artifact tree provides a structured, exportable record of the research process
* Reference IDs (`ref_0_2`) enable precise targeting for deepen and citation operations
* TTL-based expiry prevents resource leaks from abandoned sessions
* Clean separation from existing `JobStore` — no risk of breaking existing functionality
* Reuses proven Valkey patterns from the codebase (key schema, TTL, atomic ops)
* The `step()` API surface is extensible — new action types added without changing the protocol

## Negative Consequences

* Sessions are ephemeral (Valkey restart = data loss) — not suitable for persistent research
  archives (that's Phase 4: Research Memory)
* Step execution is synchronous within the API process — a long-running step (e.g., 50-URL crawl)
  blocks the client. Mitigation: complex steps can return a `step_id` for async polling.
* Valkey memory pressure if many concurrent sessions accumulate large artifacts. Mitigation:
  configurable `SESSION_MAX_ARTIFACT_BYTES` (default 10MB) with truncation warning.
* Artifact tree structure is enforced by convention, not schema — risk of structural drift
  across action types. Mitigation: `SessionManager` validates artifact structure on each append.

## Links

* Issue [#387: Session protocol](https://github.com/groktopus/groktocrawl/issues/387)
* Related: [ADR-0041: Research Memory](0041-research-memory.md)
* Related: [ADR-0042: MCP Server Architecture](0042-mcp-server-architecture.md)
* [Valkey documentation](https://valkey.io/documentation/)
