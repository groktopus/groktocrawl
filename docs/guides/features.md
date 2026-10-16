# Feature guide

## Scraping and adapters

`POST /v2/scrape` and `scrape` return clean markdown plus optional formats, quality assessment, metadata, links, screenshots, and images. Adapters run before generic fetching for supported sites and use their own fallback chains. Generic extraction applies cache revalidation, politeness/robots controls, llms.txt, markdown negotiation, browser rendering, optional recovery, and quality gates.

## Crawl, map, and monitors

`/v2/map` discovers site URLs. `/v2/crawl` runs a breadth-first crawl with sitemap modes, path filters, depth/page limits, concurrency, delay, cache age controls, deduplication, per-page results/errors, webhooks, and SSE progress. Terminal crawl responses include an `outcomeSummary` with discovered, filtered, robots-blocked, failed, barrier-rejected, duplicate, and retained counts. Empty completed crawls also carry a stable reason such as `all_urls_filtered` or `all_fetches_failed`. Monitors schedule scrape or search checks and notify configured webhooks when a change is detected.

## Search and semantic retrieval

Search uses SlopSearX for discovery and supports content enrichment, `fast`, `rich`, and deeper modes. Semantic and hybrid retrieval use the embedding service and the configured Qdrant or PostgreSQL/pgvector index; `/v2/find-similar` finds related pages from the local index or web-assisted reranking. Web similarity results include their cosine score, confidence band, raw and reranked positions, metadata-completeness flag, and search provenance so clients can distinguish weak leads from strong matches. Semantic indexing is best effort and never blocks a scrape or crawl result.

## Research, plans, sessions, and memory

The agent plans queries, discovers/scrapes sources, synthesizes cited results, and can run a gap-filling second pass. `/v2/answer` is the lower-latency grounded-Q&A path. Plans support review/execute workflows; sessions retain stepwise research context; research memory reuses compatible past artifacts; citation resolution expands compact references. Through MCP, use `create_research_plan`, inspect it with `get_research_plan`, then call `execute_research_plan` with `approve=true`; execution is one-shot and returns a job ID. MCP clients can also call `query_research_memory` before an expensive run, preserve a verified result with `store_research_memory`, retrieve it with `get_research_memory`, and use batch query/store for multiple questions. Query responses retain freshness, compatibility, similarity, memory IDs, and source metadata; delete and sweep tools are explicitly destructive. Streaming clients should render planning/source/token events incrementally and treat `done`/`error` as terminal.

## Experimental research workspace

The feature-gated research workspace exposes retained research roots, versioned evidence,
audited artifacts, and explicit revision-guarded actions in the standard deployment. It
runs alongside the existing research API and does not change `/v2/agent`, `/v2/answer`,
sessions, or research-memory behavior. Its durable ledger is separate from legacy inline
jobs, which use the queued-only redispatch and fail-closed active-job contract in
[ADR-0072](../adr/0072-valkey-leased-legacy-job-recovery.md). See the [workspace guide](research-workspace.md) and [ADR-0069](../adr/0069-adopt-bounded-research-substrate.md).

## Browser, parse, portal, and MCP

Browser routes expose short-lived Playwright sessions. Parse supports direct local document parsing and a two-step upload flow, converting Word, PowerPoint, Excel, OpenDocument, RTF, EPUB, and CSV documents via firecrawl-anydoc alongside PDF and plain text. The portal is a thin web client for agent and answer workflows. `mcp-svc` exposes GroktoCrawl capabilities as MCP tools, using the same agent API and its own session lifecycle — scrape, search, crawl, map, agent, answer, extract, enrich, batch scrape, llms.txt, citations, browser sessions, change monitors, parse, job cancellation/status, and agent-native research sessions. Session clients can create a session, add typed search/scrape/query/deepen steps, inspect or export the accumulated artifact tree, resolve source references, and delete the session. For example: `create_research_session` → `research_session_step(action="search", query="...")` → `get_research_session` → `export_research_session`. Tools carry read-only/destructive annotations for safe client use. A coverage gate (`scripts/check-mcp-coverage.py`) keeps the MCP tool surface in lockstep with the agent-svc API, mirroring the CLI coverage policy. The separate, opt-in `slopsearx-mcp` service exposes direct SlopSearX search-engine tools when the `mcp` Compose profile is enabled. Its jobs, science, research, security, and targeted sensitive-engine grants are independently configurable, but grants do not create provider credentials or bypass MCP authentication. Search results are discovery leads and snippets, not independently verified full-page evidence. See the API guide for endpoint contracts and the deployment guide for service configuration.

The feature-gated experimental browser-controller integration can set `BROWSER_CONTROL_SOCKET` to route agent health checks and browser API calls through the protected browser-controller Unix socket. Its Compose configuration mounts only the controller socket read-only, grants the socket group, and waits for controller health. Leave the variable unset for the legacy `browser-svc` HTTP route. A configured but unavailable socket fails closed; it does not fall back to TCP. This opt-in candidate wiring does not change the default deployment.

## Durable research through MCP

The separate durable research tools are `research_capabilities`,
`research_create`, `research_status`, `research_cancel`, `research_show`,
`research_artifact`, `research_evidence`, `research_delete`, `research_attach`,
`research_workspace` and `research_resume`. Check capabilities first: the server
controls availability and live versus demonstration execution. These tools
expose retained sources and reports, while the existing `agent` tool runs the
iterative research workflow. See the [durable research contract](durable-research.md)
and [workspace guide](research-workspace.md) for their limits and evidence handling.
