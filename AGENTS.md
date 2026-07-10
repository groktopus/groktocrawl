# AGENTS.md

Guide for AI coding agents working on the GroktoCrawl codebase.

## Project Overview

GroktoCrawl is a self-hosted, MIT-licensed alternative to Firecrawl. It implements the Firecrawl v2 API surface as a set of Python FastAPI services running in Docker.

## Repo Structure

```
groktocrawl/
├── agent-svc/          # Main API + agent research loop + crawl engine
│   └── agent/
│       ├── app.py      # FastAPI app factory, wires dependencies
│       ├── api.py      # Route handlers (all endpoints)
│       ├── models.py   # Pydantic request/response schemas
│       ├── worker.py   # Job processing functions (async)
│       ├── research.py # Agent research loop (search → scrape → LLM)
│       ├── scraper_client.py  # HTTP client to scraper-svc
│       ├── searxng_client.py  # Search API client
│       ├── llm.py      # OpenAI-compatible LLM client
│       ├── store.py    # Job CRUD backed by Valkey
│       ├── crawler.py  # BFS crawl orchestrator (queue, concurrency, path filtering)
│       ├── link_extractor.py  # Shared HTML link extraction (used by crawl, map, llmstxt)
│       ├── sitemap_parser.py  # XML sitemap fetcher/parser (robots.txt, common paths, nested indexes)
│       ├── dedup.py    # Multi-layer dedup (canonical tag + content hash) for crawl pages
│       ├── crawl_cache.py  # Valkey-backed response cache with maxAge/minAge semantics
│       ├── crawl_stream.py  # SSE event streaming for crawl progress
│       ├── nl_params.py # NL-to-params translation for crawl parameter derivation
│       └── tasks.py    # Background task tracker for fire-and-forget job processing
├── scraper-svc/        # URL → markdown service
│   └── scraper/
│       ├── app.py      # FastAPI, single /scrape endpoint
│       ├── fetch.py    # Three-tier fetch strategy (+ adapter dispatch)
│       ├── extract.py  # HTML → markdown conversion + content quality gates (ADR-0016)
│       └── adapters/   # Site-specific content handlers (auto-registered)
├── llm-svc/            # LLM fixture for local testing
├── test-site/          # Fixture website for integration tests
├── tests/
│   └── test_stack.py   # Integration tests
└── docker-compose.yml  # Single-file deployment
```

## Key Architecture Decisions

Architecture Decision Records (ADRs) live in `docs/adr/` and capture the context and rationale behind significant design choices. Always check the ADR index at `docs/adr/README.md` before making architectural changes — existing ADRs may document constraints or rejected alternatives that inform your approach.

### Inline async processing (no RQ worker)

Jobs are processed with `asyncio.create_task()` inside the API process. This avoids needing a separate worker container. For production deployments with high throughput, restore the RQ queue and add a worker container.

### Three-tier smart scraper

Tier 1: Check `/llms.txt` at the site root (one GET, whole site in markdown)
Tier 2: Request with `Accept: text/markdown` header (per-page markdown)
Tier 3: Playwright render + readability extraction

**Adapters run before tier 1.** When a URL matches a registered adapter, the adapter handles extraction with its own optimized fallback chain. If the adapter fails, the standard tier pipeline runs as normal. See `scraper-svc/scraper/adapters/base.py` for the adapter framework and `scraper-svc/scraper/adapters/` for available adapters.

**Current adapter categories (22 total):**
- **File/structured:** gutenberg (Project Gutenberg books as chapter-structured markdown), shopify (bypasses UCP content-negotiation trap on Shopify blog/content pages), youtube, bluesky, substack
- **Code:** github (file/repo), github-social (issues/PRs/discussions/releases)
- **Vulnerability/CVE:** nvd (NVD API, enriched), cveorg (MITRE CVE Program, authoritative)
- **Security/threat intelligence:** abuseipdb, censys, crtsh, exploitdb, hibp, mitreattack, otx, shodan, virustotal, vulncheck
- **Shared fallback:** `_helpers.py` provides `scrape_page()` for readability-lxml extraction, used by all security adapters

### LLM-agnostic

The agent service uses an OpenAI-compatible client. Swap the provider by changing `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`.

**Per-request model override:** `POST /v2/agent` accepts an optional `model` field in the request body. When set to a model name (e.g., `"gpt-4o"`), it overrides `LLM_MODEL` for that job. When omitted or `"default"`, the env-configured model is used. Wired through in `api.py` → `worker.py` → `research.py`.

**System prompt:** The agent's research behavior is defined by `SYSTEM_PROMPT` and `EXTRACT_SYSTEM_PROMPT` constants in `research.py`. These are fixed — not configurable at runtime. They instruct the LLM to evaluate source quality, synthesize across pages, detect contradictions, and cite sources.

**Search parameters:** `POST /v2/search` accepts Firecrawl v2 `sources` and `categories` dimensions alongside `query` and `limit`. These are translated to SlopSearX categories — see `searxng_client.py` for the translation maps and `docs/adr/0013-search-architecture-with-vertical-categories.md` for the architecture.

**Search type spectrum (v0.6.0):** `POST /v2/search` now accepts `search_type` (default: `fast`):
- `fast` (<1s): current behavior — raw SlopSearX results
- `rich` (1-3s): scrapes top results and enriches with LLM synthesis

Optional `output_schema` enables structured extraction from search results (single-call: search → scrape → extract). Optional `system_prompt` guides synthesis behavior. See `docs/adr/0023-search-type-spectrum-fast-and-rich.md`. The CLI exposes `--search-type` (fast/rich), `--output-schema` (JSON string or @file.json), and `--system-prompt` flags alongside existing `--sources` and `--categories`.

### Agent Endpoint with SSE Streaming

`POST /v2/agent` now supports SSE streaming via `stream: true`. Two-phase protocol:
- **Discovery:** `sources_pending` (search results found), `source_scraped` (URL fetched) — shown as they happen
- **Synthesis:** `token` events stream the LLM's output token by token
- **Final:** `done` event with full `result`, `sources` list, and `latency_ms`

When `stream` is omitted, the existing create→poll pattern is used. The CLI defaults to streaming with `--sync` to opt out.

### Grounded Q&A (`POST /v2/answer`)

A synchronous single-turn Q&A endpoint that bridges `/v2/search` and `/v2/agent`: search → scrape top results → LLM synthesis with inline citations. Designed for 1-3s latency. Request fields: `query` (required), `num_sources` (1-20, default 5), `model` (per-request LLM override), `stream` (boolean, SSE streaming). Returns `answer` (markdown with `[N]` citation markers), `sources` (list of `{url, title, relevance}`), `citations` (index→URL mapping), `search_type`, and `latency_ms`. When `stream: true`, delivers SSE events: `sources`, `token` (individual tokens), `done` (final), and `error`.

### Crawl Engine

The crawl engine (`agent-svc/agent/crawler.py`) replaces the original stub crawl with a full recursive BFS crawler that achieves Firecrawl `/v2/crawl` feature parity.

**Core modules:**

- **`crawler.py`** (`CrawlEngine`) — BFS crawl orchestrator. Manages a queue of (url, depth) tuples, enforces `max_pages` / `max_depth` limits, uses asyncio.Semaphore for configurable concurrency, supports delay-based pacing (forces sequential), integrates with the shared `LinkExtractor` for child link discovery, and writes progress to the job store for status polling. Handles per-scrape timeouts, cancellation, and maximum-duration guards.

- **`link_extractor.py`** — Shared stateless module for extracting `<a href>` links from HTML. Used by crawl, `/v2/map`, and `llmstxt.py`. Resolves relative URLs against `base_url` (or `<base>` tag), strips fragments, filters non-http/https schemes, deduplicates within a page, and classifies links as internal/subdomain/external.

- **`sitemap_parser.py`** (`SitemapParser`) — Fetches and parses XML sitemaps. Discovers sitemap URLs from robots.txt `Sitemap:` directives (preferred) and falls back to common locations (`/sitemap.xml`, `/sitemap_index.xml`). Handles sitemap index files recursively (up to 3 levels), gzipped content, and degrades gracefully on errors.

- **`dedup.py`** (`DedupManager`) — Multi-layer deduplication for crawl pages. Layer 2: canonical tag check (`<link rel="canonical">`) — if the canonical URL was already scraped, the current page is skipped. Layer 3: SHA-256 content hash — byte-for-byte identical markdown is treated as duplicate. Canonical check always runs before content hash check.

- **`crawl_cache.py`** (`CrawlCache`) — Valkey-backed response cache with `maxAge`/`minAge` semantics. Cache keys are SHA-256 hashes of URLs. Entry includes cached_at timestamp and TTL. `maxAge` serves fresh content from cache if younger than threshold; `minAge` operates in cache-only mode (cache miss returns error). Used by `CrawlEngine` before each page scrape.

- **`crawl_stream.py`** — SSE streaming support for crawl progress. Delivers per-page events (`page`, `progress`, `done`, `error`) as pages are scraped. Handles reconnection to in-progress crawls and replay of completed results.

- **`nl_params.py`** — Natural language to crawl parameters translation. Used by `POST /v2/crawl` (when `prompt` is provided) and `POST /v2/crawl/params-preview`. Calls the LLM to derive `include_paths`, `exclude_paths`, `max_depth`, and `limit` from a user's NL description. Explicitly-set parameters override LLM-derived ones.

**Crawl API endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v2/crawl` | Create a crawl job. Supports all Firecrawl v2 parameters (path filtering, sitemap modes, concurrency, delay, dedup, webhooks, SSE streaming, NL-to-params) |
| GET | `/v2/crawl/{job_id}` | Get crawl status with pagination (`next`), enhanced metadata (`created_at`, `completed_at`, `expires_at`, `duration`), and per-page enrichment (title, status_code, content_type, scraped_at, duration_ms) |
| DELETE | `/v2/crawl/{job_id}` | Cancel an in-progress crawl |
| GET | `/v2/crawl/{job_id}/errors` | Get per-URL errors and robots-blocked URLs with error types, HTTP status codes, and timestamps |
| GET | `/v2/crawl/{job_id}/stream` | SSE stream of crawl progress — delivers per-page events (`page`, `progress`, `done`, `error`) as pages are scraped; supports reconnection to in-progress crawls and replay of completed results |
| GET | `/v2/crawl/active` | List active/processing crawl jobs with crawl-specific fields (url, max_pages, max_depth, completed, total) |
| POST | `/v2/crawl/params-preview` | Preview LLM-derived crawl parameters from a natural-language prompt without starting a crawl |

**Concurrency model:** Configurable via `maxConcurrency` (1-50, default 3) with `asyncio.Semaphore`. When `delay` is set, concurrency is forced to 1 with `asyncio.sleep()` between scrapes. Valkey-backed distributed coordination is optional for multi-instance deployments.

**Data flow:** `POST /v2/crawl` → `api.py:create_crawl()` → `JobStore.create_job()` → `_process_crawl_async()` (background task) → `CrawlEngine.run()` → per-page: cache check → path filter → scraper fetch → canonical check → content hash dedup → link extraction → enqueue children → job store progress update. Webhooks fire per-page (`crawl.page`) and on completion (`crawl.completed`).

## Testing

```bash
# Full integration test against running Docker stack
cp .env.sample .env
docker compose up --build -d
docker compose exec agent-svc python3 /app/agent/tests/test_stack.py

# Or run inline (requires httpx)
pip install httpx
python tests/test_stack.py
```

The integration tests in `tests/test_stack.py` verify all endpoints against a live Docker stack with fixture services.

## Making Changes

1. Edit the relevant service code under `agent-svc/` or `scraper-svc/`
2. Rebuild: `docker compose build <service>`
3. Recreate: `docker compose up -d --force-recreate <service>`
4. Test: run the integration tests

## Adding a New Endpoint

1. Add the route handler in `agent-svc/agent/api.py`
2. Add request/response models in `agent-svc/agent/models.py`
3. If the endpoint is async (returns a job ID), **it must accept a `webhook` field** and fire it on completion/failure via `deliver_webhook()` in `agent/webhook.py`
4. Rebuild the agent-svc image
5. Add a test case in `tests/test_stack.py`

## Environment Variables

See `.env.sample` for all configurable variables. The `.env` file is loaded by `docker compose` automatically via the `env_file:` directive in `docker-compose.yml`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
