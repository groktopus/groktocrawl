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
│       ├── api.py      # Compatibility import for the routes package
│       ├── routes/     # FastAPI route handlers grouped by feature
│       ├── models.py   # Pydantic request/response schemas
│       ├── worker.py   # Job processing functions (async)
│       ├── research/   # Discovery, evidence selection, prompts and research loops
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
│       ├── fetch.py    # Multi-tier fetch/recovery orchestration (+ adapters)
│       ├── extract.py  # HTML → markdown conversion + content quality gates (ADR-0016)
│       └── adapters/   # Site-specific content handlers (auto-registered)
├── llm-svc/            # LLM fixture for local testing
├── test-site/          # Fixture website for integration tests
├── tests/
│   ├── integration/   # HTTP and multi-service acceptance tests
│   ├── service/       # Service contracts
│   └── unit/          # Unit tests
└── docker-compose.yml  # Single-file deployment
```

## Key Architecture Decisions

Architecture Decision Records (ADRs) live in `docs/adr/` and capture the context and rationale behind significant design choices. Always check the ADR index at `docs/adr/README.md` before making architectural changes — existing ADRs may document constraints or rejected alternatives that inform your approach.

### Inline async processing (no RQ worker)

Jobs are processed with `asyncio.create_task()` inside the API process. New records carry a versioned Valkey execution marker and lease: a fresh process may redispatch only jobs still queued and never claimed. Claimed work is never blindly replayed; after lease expiry it becomes `failed` with `OUTCOME_UNKNOWN`. Completion/failure webhook intents are persisted in the Valkey outbox with stable UUIDv4 `webhookId` values and retried at-least-once. Partial artifacts across Valkey, the scraper, semantic index, and research memory are not transactionally rolled back. See [ADR-0072](docs/adr/0072-valkey-leased-legacy-job-recovery.md) and the [interrupted-jobs runbook](docs/runbooks/interrupted-jobs.md); do not describe this as exactly-once execution or general resumability.

### Durable research execution

The separate `/experimental/research/v1` live run path uses server-configured search, acquisition, and model clients when its four existing feature flags are enabled and the explicit development fixture flag is false. PostgreSQL schema16 retains source bytes and audited report sets; Valkey retains scoped admission, owner generations, operation intents/receipts, and terminal projections. Do not replace a failed or unknown external operation with an unrecorded retry. The worker renews ownership; claim, publication, and cancellation serialize on a scoped PostgreSQL advisory lock, and deletion fences late publication. Recovery checks committed PostgreSQL results before invoking any adapter. Legacy `/v2` jobs keep their separate contract. Read [ADR-0070](docs/adr/0070-execute-live-durable-research.md) before changing these boundaries.

### Research-memory storage

Research-memory artifacts remain in Valkey; semantic vectors default to the
Qdrant `research_memory` collection. The explicit
`RESEARCH_MEMORY_VECTOR_MODE=shadow_pgvector` mode mirrors vectors to the
`research_memory` PostgreSQL schema while Qdrant remains authoritative.
`RESEARCH_MEMORY_VECTOR_MODE=pgvector` reads and writes the PostgreSQL
projection without contacting Qdrant. Keep the default unchanged for
unmigrated deployments, and use the explicit backfill/parity/rollback commands
in `docs/runbooks/research-memory-pgvector.md` before selecting PostgreSQL.
The semantic page index's `VECTOR_STORE_MODE` is separate; `pgvector_only`
means the page-index service has no Qdrant runtime dependency.

### Multi-tier smart scraper

Treat `scraper-svc/scraper/fetch.py:smart_scrape()` as the current orchestration
source. Adapters run before the normal pipeline; cache, binary-content handling,
quality gates and politeness also affect dispatch. The normal path includes
llms.txt, content negotiation, Playwright, FlareSolverr and LLM-assisted recovery,
with a conditional Substack browser-service fallback and best-effort outcomes.
`force_browser` takes a different path; `lightweight_only` stops before the
browser tier. Barrier recovery is centralized and does not pass barriers to
LLM recovery (see ADR-0044). This is broader than the original
three-tier description; do not assume every URL executes every stage.

Read [ADR-0001](docs/adr/0001-adapter-registry-pre-pipeline-hook.md),
[ADR-0010](docs/adr/0010-five-tier-scraper-with-llm-recovery.md),
[ADR-0016](docs/adr/0016-extraction-quality-gates.md) and
[ADR-0036](docs/adr/0036-split-scraper-fetch-modules.md), checking their statuses
and current code. Available handlers live in `scraper-svc/scraper/adapters/`;
avoid relying on a fixed adapter count.

### Research-loop changes

Use the shared [Harness Engineering procedure](https://github.com/magnus919/agent-skills/blob/main/harness-engineering/references/verification-and-improvement.md)
and [Agent Evals and Observability skill](https://github.com/magnus919/agent-skills/blob/main/agent-evals-and-observability/SKILL.md).
For local installation, load their `SKILL.md` files from the installed skill root.
Apply the repository-specific [ADR-0076 evaluation contract](docs/adr/0076-bound-research-loop-evaluation.md)
for research-loop proposals, probes, composition and promotion. Its proposed
status does not authorize runtime changes, experiments, release or deployment.

### LLM-agnostic

The agent service uses an OpenAI-compatible client. Swap the provider by changing `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`.

**Per-request model override:** `POST /v2/agent` accepts an optional `model` field in the request body. When set to a model name (e.g., `"gpt-4o"`), it overrides `LLM_MODEL` for that job. When omitted or `"default"`, the env-configured model is used. Wired through in `routes/agent.py` → `worker.py` → `research/loop.py`.

**System prompt:** The agent's research behavior is defined by `SYSTEM_PROMPT` and `EXTRACT_SYSTEM_PROMPT` constants in `research/prompts.py`. These are fixed — not configurable at runtime. They instruct the LLM to evaluate source quality, synthesize across pages, detect contradictions, and cite sources.

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

**Data flow:** `POST /v2/crawl` → `routes/crawl.py:create_crawl()` → `JobStore.create_job()` → `_process_crawl_async()` (background task) → `CrawlEngine.run()` → per-page: cache check → path filter → scraper fetch → canonical check → content hash dedup → link extraction → enqueue children → job store progress update. Webhooks fire per-page (`crawl.page`) and on completion (`crawl.completed`).

## Testing

Run from a disposable development checkout, with Docker and `uv` installed.
Do not overwrite an existing deployment's `.env`.

```bash
# Create .env from .env.sample only if this checkout has no configuration.
test -f .env || cp .env.sample .env
docker compose --profile fixture up --build -d
uv sync --locked --no-dev --group fast-tests

# Basic HTTP health checks against the local stack; these are not the full suite.
uv run --no-sync pytest tests/integration/test_stack.py::test_services_health \
  tests/integration/test_stack.py::test_scraper_health_reports_playwright \
  -n 0 --no-cov
```

Tests live under `tests/integration/`, `tests/service/` and `tests/unit/`.
Production images do not include the test suite. CI copies checkout tests and
installs their locked dependencies before running the full container suite.
See [CONTRIBUTING.md](CONTRIBUTING.md#running-tests) for fast tests and the
workflow definitions for isolated search/model fixtures, storage and recovery
acceptance. Override `AGENT_BASE_URL` and `SCRAPER_BASE_URL` for non-default
local ports; do not point these checks or acceptance fixtures at production.

## Pull Requests, Review, and Merging

**CI on every PR:** Fast Tests, Code Quality (vulture/deptry/mypy/ruff/jscpd/gitleaks),
Docker/Integration Tests (self-hosted runner, runs when runtime code changes), and Architecture CI.
The `main required checks` ruleset requires `Code Quality Gate` and `Runtime Gate`.

**AI review loop (do not chase):** the `Droid Auto Review` workflow (`droid-review.yml`) publishes
its findings or no-findings summary as a PR review/comment under the `factory-droid` bot and NEVER
approves. A green workflow job alone does not prove the review was published; verify the visible PR
review/comment includes the result and finding counts. A successful review with no findings is valid
when the published summary says so. Each re-run re-scans the whole diff and mints new (often minor)
findings, so re-triggering it after every fix is an unbounded loop. Treat "review passed" as: the
published result from ONE review pass is visible, all findings from that pass are fixed, CI is green,
and no blocking (P1/P2) findings remain — do not iterate until the bot reports zero findings.

The workflow uses Nous Portal's `openai/gpt-6-luna` through `NOUS_API_KEY`. The Droid action's debug
artifact upload is disabled because its `~/.factory` files can contain resolved provider credentials.

**Merging to `main`:** gated by the `main review policy` ruleset (1 approving review + resolved review
threads). Because the AI reviewer does not approve and human approval is often unavailable to automated
agents, the maintainer (`magnus919`) is a configured bypass actor. Merge with:
`gh pr merge <PR> --merge --delete-branch --admin`
only after CI is green and review findings are addressed.

## Making Changes

1. Edit the relevant service code under `agent-svc/` or `scraper-svc/`
2. Rebuild: `docker compose build <service>`
3. Recreate: `docker compose up -d --force-recreate <service>`
4. Test: run the integration tests

## Adding a New Endpoint

1. Add the handler in the appropriate `agent-svc/agent/routes/` module and register new routers in that package
2. Add request/response models in `agent-svc/agent/models.py`
3. If the endpoint is async (returns a job ID), **it must accept a `webhook` field** and persist terminal delivery intent through the Valkey job outbox with a stable `webhookId`. Direct `deliver_webhook()` remains best-effort for callers outside that outbox; see [ADR-0072](docs/adr/0072-valkey-leased-legacy-job-recovery.md).
4. Rebuild the agent-svc image
5. Add endpoint contract tests under `tests/service/` and user-workflow acceptance under `tests/integration/`; use the isolated CI setup for dependencies.

## Environment Variables

See `.env.sample` for all configurable variables. The `.env` file is loaded by `docker compose` automatically via the `env_file:` directive in `docker-compose.yml`.

## graphify

A local knowledge graph may be available at graphify-out/ with god nodes, community
structure, and cross-file relationships. If its report or graph is absent, state
that limitation and use current source and ADRs; do not assert unavailable
artifacts were read.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

### Query tuning for this repo

The graph's node labels are code identifiers and doc headings, not natural language. `graphify query` matches labels one-directionally, so wording mismatches return noise. Verified patterns (see `graphify-out/memory/` for seeded Q&As):

- **Expand vocabulary to graph labels first.** The label set is small; grep it before querying when a question uses non-identifier words. Examples: "deduplication" → `dedup` / `DedupManager`; "crawl engine" → `CrawlEngine`; "link extraction" → `extract_links()`. Ask `graphify explain "<symbol>"` to confirm exact names.
- **Avoid generic terms that exact-match endpoint names.** `crawl()`, `search()`, `map()`, `agent()` in `mcp-svc/mcp_server.py` get an exact-match bonus and flood results with the whole MCP endpoint community. Prefer file/module names (`crawler.py`) or specific classes (`CrawlEngine`).
- **`--context import` for dependency questions** (edges filtered to imports only; e.g. `graphify query "dedup manager" --context import` returns ~19 focused nodes instead of 400+). `--context call` works for call chains. Requires graphify >= 0.6.7 — older versions silently ignore the flag.
- **`--dfs` traces a chain; `--budget N` raises the output cap** (default 2000 tokens). Neither fixes bad seeds — fix seeds first via vocabulary.
- **After answering a graph question, run `graphify save-result --question "<verbatim question>" --answer "<answer>" --nodes <labels cited>`** so the Q&A becomes a node on the next `graphify update` (feedback loop, no API cost).


## Experimental research substrate

Live durable HTTP execution uses `experimental/live_runner.py`, recorded query planning and `DurableResearchLedger`; see [ADR-0070](docs/adr/0070-execute-live-durable-research.md) and the [execution contract](docs/guides/durable-research.md). Preserve admitted policy versions, exact operation receipts, fencing and atomic PostgreSQL publication. Never replay an ambiguous paid dispatch. Terminal callbacks have a separate durable outbox and stable event ID; callback retries must not restart research. `as_of` is an evidence evaluation target, not archived capture. Test changed execution through the public HTTP route with isolated stores as well as interruption boundaries.

The research architecture in `agent-svc/agent/experimental/` is adopted for the standard deployment under [ADR-0069](docs/adr/0069-adopt-bounded-research-substrate.md). Existing APIs, jobs, sessions, and research-memory behavior remain compatible; legacy inline-job recovery follows [ADR-0072](docs/adr/0072-valkey-leased-legacy-job-recovery.md). Do not infer deployment readiness from an accepted experiment ADR or the presence of code or environment variables. Before changing this area, read ADR-0069 and the historical [GroktoCrawl X ADR archive](docs/adr/experimental/groktocrawl-x/README.md); archived acceptance is scoped to the former experiment. Keep migrations additive, explicit, versioned, and reversible. Feature flags are rollout controls and may be enabled in the standard deployment after the documented migration and verification checks pass.
