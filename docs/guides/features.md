# Feature guide

## Scraping and adapters

`POST /v2/scrape` and `scrape` return clean markdown plus optional formats, quality assessment, metadata, links, screenshots, and images. Adapters run before generic fetching for supported sites and use their own fallback chains. Generic extraction applies cache revalidation, politeness/robots controls, llms.txt, markdown negotiation, browser rendering, optional recovery, and quality gates.

## Crawl, map, and monitors

`/v2/map` discovers site URLs. `/v2/crawl` runs a breadth-first crawl with sitemap modes, path filters, depth/page limits, concurrency, delay, cache age controls, deduplication, per-page results/errors, webhooks, and SSE progress. Monitors schedule scrape or search checks and notify configured webhooks when a change is detected.

## Search and semantic retrieval

Search uses SlopSearX for discovery and supports content enrichment, `fast`, `rich`, and deeper modes. Semantic and hybrid retrieval use the embedding service and Qdrant; `/v2/find-similar` finds related pages from the local index or web-assisted reranking. Semantic indexing is best effort and never blocks a scrape or crawl result.

## Research, plans, sessions, and memory

The agent plans queries, discovers/scrapes sources, synthesizes cited results, and can run a gap-filling second pass. `/v2/answer` is the lower-latency grounded-Q&A path. Plans support review/execute workflows; sessions retain stepwise research context; research memory reuses compatible past artifacts; citation resolution expands compact references. Streaming clients should render planning/source/token events incrementally and treat `done`/`error` as terminal.

## Browser, parse, portal, and MCP

Browser routes expose short-lived Playwright sessions. Parse supports direct local document parsing and a two-step upload flow. The portal is a thin web client for agent and answer workflows. `mcp-svc` exposes GroktoCrawl capabilities as MCP tools, using the same agent API and its own session lifecycle. See the API guide for endpoint contracts and the deployment guide for service configuration.
