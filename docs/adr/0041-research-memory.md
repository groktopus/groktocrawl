# Research Memory — Cross-Session Semantic Cache

* Status: proposed
* Deciders: magnus
* Date: 2026-07-03

Technical Story: When an agent or user asks a question similar to one GroktoCrawl has already
researched, the system should return the cached research artifact rather than re-executing
the full search → scrape → LLM pipeline. This saves credits, reduces latency, and provides
persistent research archives across sessions.

- Research-substrate scope: Existing semantic-cache behavior remains unchanged. The opt-in research-substrate scope follows [ADR-0069](0069-adopt-bounded-research-substrate.md); the former experiment decision records are preserved in the [provenance archive](experimental/groktocrawl-x/README.md).

## Context and Problem Statement

Every `POST /v2/agent` call executes the full research pipeline: search, scrape, LLM synthesis.
This is wasteful when:

1. **Similar questions recur.** "What is the current state of AI regulation in the EU?" asked
   an hour apart should not trigger two full research cycles.

2. **Research artifacts have reuse value.** A thorough investigation of a topic is valuable
   beyond the immediate query — related questions may benefit from it.

3. **Agents re-execute expensive pipelines.** An AI agent exploring a topic may ask closely
   related questions that could share cached artifacts.

4. **No persistent record of past research.** Unlike crawl results (which are cached per-URL
   via `CrawlCache`), agent research has no persistent storage — results exist only in the
   job store and expire after 24 hours.

The solution is a research memory system: a hybrid semantic cache that stores research
artifacts in Valkey (for fast retrieval) and uses Qdrant (already available via `semantic-svc`)
for similarity-based lookup.

## Decision Drivers

* Must reduce redundant LLM calls (credit savings) and redundant scraping (bandwidth/politeness)
* Cache hits must be semantically similar, not just exact text matches
* Must work with the existing `semantic-svc` (Qdrant) infrastructure — no new vector database
* Cache entries must expire (stale research data is actively harmful)
* Must be scoped per-user or per-API-key (configurable)
* Must not slow down cache misses — the lookup must be fast and non-blocking
* Must be backward compatible — existing agent calls without memory work exactly as before

## Considered Options

### Option A: Valkey-Only Semantic Cache (Text Hashing)

Use Valkey with fuzzy text hashing (MinHash, SimHash) to find similar queries. Store research
results keyed by query hash.

**Pros:**
- No additional service dependency (Valkey only)
- Simple: hash the query, check cache, return or run

**Cons:**
- MinHash/SimHash captures lexical similarity, not semantic similarity
- "EU AI regulation 2026" and "What are the new rules for artificial intelligence in Europe?"
  would NOT match despite being semantically identical
- No way to tune similarity threshold beyond hash collision probability
- Would require a new embedding model or library in agent-svc regardless

### Option B: Qdrant-Only Semantic Cache

Store both embeddings and full artifacts in Qdrant. Use Qdrant's built-in payload storage for
the research artifact and metadata.

**Pros:**
- Single service for both similarity search and storage
- Qdrant's payload filtering enables per-user scoping via metadata tags
- No dual-storage synchronization issues

**Cons:**
- Qdrant is not designed for large binary payloads — artifacts with sources can be 100KB-1MB
- Qdrant payload size limits may constrain artifact storage (practical limit ~100KB per point)
- Qdrant restart is slower than Valkey restart (index rebuild)
- Introduces qdrant client dependency in agent-svc (currently only semantic-svc talks to qdrant)

### Option C: Hybrid — Valkey Storage + Qdrant Similarity Search (Chosen)

Store full research artifacts in Valkey (supports large values, TTL, fast key-value access).
Store embeddings + query text + Valkey key reference in Qdrant for similarity search. The
`semantic-svc` provides embedding generation and similarity search via its existing API.

**Pros:**
- Valkey handles large payloads natively (up to 512MB per key)
- Qdrant handles the thing it's best at: high-dimensional similarity search
- No new service dependencies — both Valkey and semantic-svc already exist
- Embedding model (BAAI/bge-m3, 1024-dim) is already deployed and working
- TTL is Valkey-native, no application-level sweep needed
- Per-user scoping via Qdrant payload filters on `user_id` / `api_key` field
- Clean separation: semantic-svc owns embeddings/similarity, agent-svc owns research logic

**Cons:**
- Two storage systems to coordinate (Valkey + Qdrant)
- Cache miss still requires one semantic-svc round-trip for the embedding lookup
- Stale Qdrant entries (where Valkey key expired) need periodic cleanup

## Decision Outcome

Chosen option: **Option C — Hybrid: Valkey Storage + Qdrant Similarity Search**

### Architecture

```
Cache Write (on agent completion):
  agent result → embed(query) via semantic-svc → store artifact in Valkey
    → upsert embedding + {query, valkey_key, user_id, timestamp} in Qdrant

Cache Read (on agent request):
  agent request → embed(query) via semantic-svc → search Qdrant for similar
    → if similarity > threshold → fetch artifact from Valkey → return cached
    → if no match → run normal research pipeline → store result

TTL & Eviction:
  Valkey: SET with EX (default 7 days, configurable via RESEARCH_MEMORY_TTL)
  Qdrant: points tagged with expires_at timestamp
  Periodic sweep: agent-svc background task removes expired Qdrant points
```

### Component: `ResearchMemory`

```python
class ResearchMemory:
    def __init__(self, redis_url: str, semantic_url: str, config: MemoryConfig)

    async def query(self, query: str, user_id: str | None = None) -> MemoryResult | None
        # 1. Get embedding for query via semantic-svc POST /v1/embeddings
        # 2. Search Qdrant for top-k similar entries
        # 3. Filter by similarity threshold (configurable, default 0.85)
        # 4. If match: fetch artifact from Valkey, check TTL freshness
        # 5. Return MemoryResult(cached=True, artifact, similarity, age, freshness)
        #    or MemoryResult(cached=False) on miss

    async def store(self, query: str, artifact: str, sources: list[dict],
                    user_id: str | None = None) -> str
        # 1. Generate memory_id (UUID v4)
        # 2. Store artifact + sources in Valkey: memory:{memory_id}:data (with TTL)
        # 3. Get embedding for query via semantic-svc
        # 4. Upsert into Qdrant: {embedding, query, memory_id, user_id, timestamp, expires_at}
        # 5. Return memory_id

    async def delete(self, memory_id: str) -> None
        # Remove from both Valkey and Qdrant

    async def sweep(self) -> int
        # Remove expired entries from Qdrant (Valkey keys auto-expire)
        # Returns count of swept entries
```

### Valkey Key Schema

```
memory:{memory_id}:data  → JSON {query, artifact, sources, model, created_at, expires_at, user_id}
memory:index             → SET of all active memory_ids (for sweep operations)
```

- TTL: configurable via `RESEARCH_MEMORY_TTL` (default 604800 = 7 days)
- Maximum artifact size: `RESEARCH_MEMORY_MAX_ARTIFACT_BYTES` (default 5MB) — larger artifacts
  are stored but a warning is logged

### Qdrant Collection

Collection name: `research_memory` (created on first use if not exists)

Point payload:
```json
{
  "query": "original query text",
  "memory_id": "uuid",
  "user_id": "user-123 | null",
  "timestamp": "2026-07-03T12:00:00Z",
  "expires_at": "2026-07-10T12:00:00Z"
}
```

### Semantic Service Integration

`ResearchMemory` calls the existing `semantic-svc` HTTP API:
- `POST /v1/embeddings` → `{embedding: [1024 floats]}` — for query embedding
- `POST /v1/search` → `{results: [{id, score, payload}]}` — for similarity search (uses
  Qdrant internally, already supports payload filters for user_id scoping)

No new endpoints needed in `semantic-svc`. No new embedding models needed — reuse BAAI/bge-m3.

### Freshness and Staleness

Cache entries have a freshness classification:

| Age | Classification | Behavior |
|-----|---------------|----------|
| < TTL / 4 | Fresh | Return cached result, no warning |
| TTL / 4 to TTL / 2 | Aging | Return cached result with `freshness: "aging"` flag |
| > TTL / 2 | Stale | Return cached result with `freshness: "stale"` flag + recommendation to re-research |
| > TTL | Expired | Valkey key auto-deleted, Qdrant point swept. Cache miss. |

The `freshness` flag is returned to the agent, which can decide to accept the cached result
or request a fresh research cycle. A `force_fresh: true` parameter on the agent request skips
the cache entirely.

### Per-User Scoping

When `API_KEY` authentication is enabled, the `user_id` is derived from the API key hash.
Qdrant payload filters scope similarity search to the same user:
- With auth: `must: [{key: "user_id", match: {value: user_id}}]`
- Without auth: no filter (shared cache across all users)

This is configurable via `RESEARCH_MEMORY_SCOPE` (values: `global` or `per_user`, default `global`)

### Integration with Agent Pipeline

In `worker.py`, the `_process_agent_async()` function is extended:

```python
async def _process_agent_async(job_id, prompt, ...):
    # NEW: Check research memory before pipeline
    memory_result = await research_memory.query(prompt, user_id)
    if memory_result and memory_result.cached and not force_fresh:
        # Return cached artifact immediately
        store.complete_job(job_id, memory_result.to_response())
        return

    # Existing pipeline
    result = await run_research(prompt, ...)

    # NEW: Store result in research memory
    await research_memory.store(prompt, result["answer"], result["sources"], user_id)

    store.complete_job(job_id, result)
```

### Cache Invalidation

- **TTL-based**: Primary mechanism. Entries expire naturally.
- **Explicit delete**: `DELETE /v2/research-memory/{memory_id}` (if admin API is desired)
- **Force refresh**: `POST /v2/agent {force_fresh: true}` skips cache
- **Sweep**: Background task runs every 15 minutes via `asyncio.create_task()`, removes
  Qdrant points whose Valkey keys have expired (belt-and-suspenders with TTL)

## Positive Consequences

* Reduces LLM credits consumed by ~30-50% for common research topics (estimated)
* Reduces scrape latency for cached results (from 15-30s to <1s for cache hits)
* Provides persistent research record across sessions (unlike ephemeral job store)
* Freshness flags give agents control over staleness tolerance
* No new infrastructure — reuses Valkey, Qdrant, and the existing embedding model
* Semantic similarity (not exact match) means rephrased questions get cache hits
* Per-user scoping prevents data leakage in multi-tenant deployments

## Negative Consequences

* Embedding generation adds ~50-100ms latency per query (cache check overhead)
* Qdrant collection grows unbounded without sweep (mitigated by periodic background sweep)
* Cache misses still pay the embedding cost (one extra semantic-svc round-trip)
* Stale entries may return outdated information if freshness flags are ignored
* Two storage systems mean two points of failure for cache integrity — if Valkey key is
  missing but Qdrant point exists, the cache "hit" returns an error
* Embedding model (BAAI/bge-m3) is general-purpose, not optimized for research questions —
  may produce false negatives for highly technical/specific queries

## Links

* Issue [#391: Research memory](https://github.com/groktopus/groktocrawl/issues/391)
* Related: [ADR-0040: Session Protocol](0040-session-protocol.md)
* Related: [ADR-0042: MCP Server Architecture](0042-mcp-server-architecture.md)
* [ADR-0019: Intelligent Scrape Cache](0019-intelligent-scrape-cache.md) — precedent for
  TTL-based caching patterns in this codebase
* [semantic-svc documentation](../semantic-svc/)
