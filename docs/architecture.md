# GroktoCrawl architecture

## System context

```mermaid
flowchart LR
    client["API clients / CLI / portal / MCP clients"] --> agent["agent-svc\nFastAPI public API"]
    agent <--> valkey[("Valkey\njobs, sessions, cache")]
    agent --> scraper["scraper-svc\nfetch and extraction"]
    agent --> search["SlopSearX\nweb discovery"]
    agent --> semantic["semantic-svc\nembeddings and reranking"]
    semantic <--> qdrant[("Qdrant\nvector index")]
    agent --> llm["OpenAI-compatible LLM"]
    scraper --> browser["browser-svc\nPlaywright sessions"]
    scraper -. optional .-> flare["FlareSolverr"]
    portal["portal-svc"] --> agent
    mcp["mcp-svc"] --> agent
    ofelia["Ofelia scheduler"] -. monitor runs .-> agent
    parse["parse-svc"] --> agent
```

`agent-svc` owns the public `/v2` API, authorization, request IDs, health and metrics. It composes domain routers from `agent/routes/`, owns background tasks through `TaskTracker`, and stores persistent job records in Valkey. Jobs are processed in-process with `asyncio.create_task()`; there is no external job owner or worker queue.

## Main execution paths

```mermaid
flowchart TD
    request["Request"] --> route["Domain route"]
    route --> direct{"Synchronous?"}
    direct -->|yes| client["Service client / domain module"]
    direct -->|no| store["Create Valkey job"] --> task["TaskTracker background task"]
    task --> worker["worker.py / CrawlEngine / monitor"]
    worker --> store
    worker --> hook["Webhook or SSE event"]
    client --> response["Response"]
    store --> response
```

### Asynchronous job durability contract

Valkey stores job metadata, status, completed results, execution leases, and a webhook outbox. `TaskTracker` gives in-flight work a five-second grace period during an orderly shutdown, then cancels remaining tasks. At startup, only jobs still queued and never claimed are reconstructed from their normalized request. A claimed job whose owner disappears is not replayed; after lease expiry it becomes `failed` with `OUTCOME_UNKNOWN`.

After process loss:

- An old record without an execution marker is treated as `OUTCOME_UNKNOWN`; a newly queued record may be dispatched once through the fenced owner.
- A cancellation request atomically records cancellation and revokes the current fence. The worker task itself remains process-local.
- Partial artifacts already written to Valkey, Qdrant, or another downstream system can remain; there is no transaction or rollback across those stores.
- Completion and failure webhook intent is committed with the terminal state and replayed at-least-once with the original `webhookId`; receivers must deduplicate it.

This is fail-closed recovery, not general resumability or exactly-once execution. The owner and remaining consistency limits are specified in [ADR-0072](adr/0072-valkey-leased-legacy-job-recovery.md), which updates the historical decision in [ADR-0047](adr/0047-defer-restart-safe-execution.md). Operators should follow the [Interrupted Jobs runbook](runbooks/interrupted-jobs.md). This boundary extends the best-effort decision in [ADR-0035](adr/0035-graceful-shutdown.md).

Research code lives in `agent/research/`: it plans a query, discovers sources, scrapes them, synthesizes with the LLM, optionally detects gaps for another pass, and can stream progress and tokens. Research memory combines Valkey metadata with semantic lookup. Crawl uses a breadth-first engine with sitemap discovery, path filters, concurrency, cache controls, robots/politeness handling, canonical/content deduplication, and SSE progress.

## Scraping pipeline

```mermaid
flowchart LR
    url["URL"] --> adapter{"Adapter match?"}
    adapter -->|yes| specialized["Specialized adapter chain"]
    specialized --> accepted{"Quality acceptable?"}
    adapter -->|no| cache["Cache and politeness"]
    accepted -->|no/failure| cache
    cache --> tier1["Tier 1: llms.txt"] --> tier2["Tier 2: text/markdown negotiation"] --> tier3["Tier 3: Playwright / browser service"]
    tier3 --> shield["Optional FlareSolverr / recovery"]
    shield --> quality["Quality gates + metadata"]
    accepted -->|yes| quality
    quality --> result["Markdown, formats, quality, metadata"]
```

Adapters cover publishing and media, source code and GitHub social content, recruitment, commerce, public-domain books, and security/threat intelligence. They are registered in-process and use per-adapter fallback chains; the generic pipeline remains the final fallback.

## Semantic retrieval

`semantic-svc` loads its embedding model during application lifespan and exposes embedding, reranking, index, migration, retention, and search routers. Qdrant persists named-vector collections. `GET /health` is the backward-compatible liveness and component-detail surface; `GET /ready` returns HTTP 503 until the models and configured serving vector store are available. Compose uses `/ready` for routing readiness. `agent-svc` treats indexing as best effort, so a semantic-service outage does not fail a scrape or crawl request.

## Performance telemetry

Stage-level latency and capacity signals are recorded with bounded labels and exported as `groktocrawl_*` metrics (see [ADR-0048](adr/0048-stage-level-telemetry.md) and `docs/observability.md`). Research stages, cache lookups, browser semaphore saturation, scrape-tier/adapter outcomes, active-work/cancellation counts, and streaming time-to-first-event/token are all observable per service. `benchmarks/run_benchmarks.py` reproduces p50/p95 across cold/warm/lightweight/browser fixtures and writes a portable, checked-in baseline artifact — the evidence gate for future browser-process-reuse and adapter-investment decisions.

## Operational boundaries

- Valkey is the shared state boundary for jobs, monitor/session data, and caches.
- Qdrant is the persistent vector-index boundary.
- SlopSearX and the configured LLM are external capabilities; their availability is surfaced through health checks and request failures/degradation.
- `portal-svc` and `mcp-svc` are thin clients of the public agent API; they do not own duplicate business logic.
- `ofelia` triggers scheduled monitor execution through the agent service.

## Decisions

Accepted decisions describe the architecture currently relied upon; proposed ADRs describe possible future work and are not implementation commitments. See the [ADR index](adr/README.md), especially accepted ADRs for adapters, webhooks, observability, crawl, settings, lifecycle management, and SlopSearX migration.
