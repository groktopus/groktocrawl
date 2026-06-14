# Changelog

All notable changes to GroktoCrawl are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Consolidated urlparse imports into shared `common/url.py`** — new module with `normalize_url()`, `extract_domain()`, `is_same_origin()`, and `is_private_host()`. Refactored 17+ inline `urlparse` call sites across agent-svc, scraper-svc, and browser-svc. Pure refactor — no behavior changes. Dockerfiles updated with `COPY common/ common/`. 28 new unit tests. (closes #194)

### Added

- **Project Gutenberg adapter** — extracts books as chapter-structured markdown. Three-tier fallback chain: EPUB → plain text → generic pipeline. Zero new dependencies. Enriches metadata via Gutendex API (title, author, subjects, language). Registered at priority 200. See `scraper-svc/scraper/adapters/gutenberg.py`. (closes #181)

- **Batch vector ingestion via Qdrant gRPC (ADR-0030)** — adds `POST /index/batch` to semantic-svc for batched embedding and Qdrant upsert. Batch scrape and crawl workers now accumulate pages and fire a single batch call instead of N per-page calls. Expected: 500-page crawl indexing drops from ~50s to ~250ms (200x). New tests: `test_batch_index_endpoint`, `test_batch_index_empty`. Legacy flat-vector Qdrant collections auto-migrate to named vectors on startup. See `docs/adr/0030-batch-vector-ingestion.md`. (closes #154)

- **Service-level metrics for semantic-svc (ADR-0029)** — adds Prometheus-compatible `/metrics` endpoint to semantic-svc with stdlib-based OpenMetrics format (no new dependencies). Metrics tracked: document count gauge (`groktocrawl_index_docs_total`), eviction counter (`groktocrawl_index_evictions_total`), request latency histogram per endpoint (`groktocrawl_index_query_duration_seconds`), embedding inference duration (`groktocrawl_index_embeddings_duration_seconds`), and request counter per endpoint (`groktocrawl_search_requests_total`). ASGI middleware instruments all 11 existing endpoints automatically. Eviction counter tracks cumulative evictions via `_evict_if_needed()`. See `docs/adr/0029-service-level-metrics-for-semantic-svc.md`. (closes #153)

- **Embedding model migration path — named vectors, backfill, dual-write, cutover (ADR-0028)** — supports upgrading the embedding model without dropping the index. Uses Qdrant named vectors for per-point multi-model storage. New endpoints: `GET /index/model` (model config + migration state), `POST /index/migrate/start` (begin backfill), `GET /index/migrate/status` (progress), `POST /index/migrate/cutover` (switch queries). New payload fields: `embedding_model`, `embedding_dim`, `embedding_models` (history). Dual-write mode indexes with both old and new model during migration. Old vectors retained after cutover for rollback safety. See `docs/adr/0028-embedding-model-migration-path.md`. (closes #155)

- **Unit test coverage for worker.py and research.py** — adds `test_worker.py` (19 tests, 693 lines) covering all 7 worker processing functions, and 23 new tests for previously uncovered research.py functions (`run_extract`, `run_research_stream`, `run_answer_stream`, `run_rich_search`, `_rerank_answer_sources`). Agent-svc/agent coverage from ~15% to 55%. Coverage threshold raised to 20%. (closes #192)

---

## [0.7.0] — 2026-06-09

### ⚠️ Breaking Changes

- **Two new required services** — `semantic-svc` and `qdrant` are now part of the docker-compose stack. `docker compose up` will fail if these images cannot be pulled. First-time startup requires pulling the Qdrant image, and the semantic-svc needs to create the initial collection — expect a longer first spin-up.
- **New optional service** — `portal-svc` (port 8082:8081) added to docker-compose. Not required for API operation but published by default.
- **Scraper-svc now loads `.env`** — the scraper container has `env_file: .env` in docker-compose.yml. Existing deployments with custom `.env` files will see new env vars picked up automatically.
- **`search_type` defaults to `fast` on `/v2/search`** — this matches existing behavior but is now an explicit parameter. The new `rich` mode is opt-in.

### 🧠 Semantic Search Engine (Phase 1 & 2)

GroktoCrawl evolves from a stateless scraper into a learning search engine with a persistent vector index and ad-hoc semantic reranking.

- **Phase 1: Semantic reranking (#142)** — `semantic-svc` performs embedding-based cosine reranking of SearXNG results on every `/v2/search` call. Uses pure-Python dot product (no numpy dependency, #143). Wired through the `/v2/answer` endpoint via `retrieval_mode` parameter (#156).
- **Phase 2: Persistent vector index with Qdrant (#145)** — every page GroktoCrawl scrapes, crawls, or maps is embedded and stored in a Qdrant vector index (up to 250K docs by default). Subsequent searches query both SearXNG and the local corpus — the project's accumulated knowledge becomes searchable. Qdrant v1.18.2 pinned for reproducibility (#158).
- **Batch vector ingestion via Qdrant gRPC (#163)** — large crawl results are ingested in batches for efficiency. New `semantic-svc/semantic/index.py` module.
- **Content-based near-duplicate detection (#157)** — configurable cosine threshold (default 0.95) identifies duplicates at indexing time. `NEAR_DUP_THRESHOLD` and `NEAR_DUP_MODE` (skip/update) env vars.
- **Smarter index retention — domain TTLs, frequency weighting, access boosting (#159, ADR-0027)** — replaces simple LRU eviction with a scoring function that considers content type, crawl frequency, and access patterns. News/social evicts first; reference/docs content persists longest.
- **Embedding model migration path (#160)** — named vectors support enables future embedding model upgrades with backfill, dual-write during migration, and clean cutover.
- **Service-level metrics for semantic-svc (#161, ADR-0029)** — stdlib-based OpenMetrics: `groktocrawl_index_docs_total`, `groktocrawl_index_evictions_total`, `groktocrawl_index_query_duration_seconds`, `groktocrawl_index_embeddings_duration_seconds`.
- **Thread executor fix (#166)** — `model.encode()` now runs in a thread executor to avoid blocking the async event loop, fixing concurrent request handling under load.
- **Qdrant API compatibility fixes (#146, #165)** — corrects point ID type (uint64 via SHA-256 truncation), uses `using` instead of `query_vector_name` for qdrant-client 1.18 compatibility.

### 🌐 Web Portal (portal-svc)

A human-facing web interface for GroktoCrawl, available at port 8082.

- **Single-search-bar UI (#132, ADR-0021)** — FastAPI + Jinja2 interface with Google-inspired design. Routes queries to `/v2/answer` with SSE streaming, real-time token output with citations, and a recent queries sidebar (localStorage-persisted).
- **Client-side markdown rendering** — uses the `marked` library for full GFM rendering of answer text (tables, bold, inline code, links).
- **Answer-above-sources layout** — primary content displayed first, with scrollable source citations below.
- **Placeholder deep research button** — reserved for future v0.2 integration with agent-SSE.

### 🎤 Grounded Q&A (/v2/answer)

A new synchronous single-turn Q&A interface sits between `/v2/search` (raw results) and `/v2/agent` (deep multi-step research), giving users a fast grounded-answer path.

- **POST /v2/answer endpoint (#117, ADR-0017)** — pipeline: search → scrape → LLM synthesis → citations in one round-trip. Returns structured response with `answer` (markdown), `sources` (list), `citations` (index↔URL mapping), `search_type`, and `latency_ms`.
- **SSE streaming** — `stream: true` enables token-by-token streaming with source discovery events before LLM output. Reuses protocol from ADR-0017.
- **Concurrent scraping with early sources emission (#135)** — `_scrape_urls()` now scrapes in parallel with semaphore control, emitting sources as they complete instead of waiting for all.
- **Retry on scrape failure (#120)** — loops through search results until enough sources succeed (bounded by 2x requested count). Prevents empty responses when top results are from hostile sites but usable sources exist deeper.
- **Video platform URL deprioritization (#138)** — YouTube, Vimeo, and Twitch URLs are scored lower in source selection, preferring text-based sources.
- **`answer` CLI subcommand (#118)** — `groktocrawl answer "question"` with `--sources`, `--model`, `--num-sources`, and streaming defaults.

### 🔌 New Site Adapters

Building on the adapter framework introduced in v0.6.0, three new adapters add structured extraction for high-value content sources.

- **GitHub adapter (#115)** — `scraper-svc/scraper/adapters/github.py` for raw file content, blob URLs, repo roots (README + metadata), and tree listings. Priority 200.
- **GitHub social adapter (#115)** — `scraper-svc/scraper/adapters/github_social.py` for issues, PRs, discussions, releases, and commits via GraphQL API (v4). Three-tier fallback: GraphQL → REST → HTML scrape. Priority 190.
  - `GITHUB_TOKEN` env var enables 5,000 req/hr + GraphQL.
  - Works without a token at 60 req/hr (REST) or unauth (HTML scrape).
- **Substack adapter (#122)** — three-tier extraction via RSS feed (primary, no auth) → readability-lxml → browser-svc. Detects both `*.substack.com` and vanity domains via RSS fingerprinting. 26 unit tests.
- **Reddit adapter (#121)** — post and comment extraction via the JSON API (`.json` suffix).

### 🔍 Search Improvements

- **Search type spectrum — fast and rich (#140, ADR-0023)** — `/v2/search` accepts `search_type` (default: `fast`). `fast` is instant (existing behavior). `rich` mode scrapes top results and enriches with LLM synthesis (1-3s). Optional `output_schema` enables structured data extraction in a single call.
- **Agent SSE streaming (#137, ADR-0022)** — `/v2/agent` now supports `stream: true` for Server-Sent Events. Two-phase streaming: discovery events followed by token-by-token LLM output. CLI defaults to streaming; `--sync` to opt out.
- **Vertical search categories (#101, #102)** — `sources` and `categories` parameters on `/v2/search` translated to SearXNG-native categories.

### 🛡️ Politeness Protocol & Proxy Support

- **Politeness protocol (#114)** — new `scraper-svc/scraper/politeness.py` module. Gated behind `SCRAPER_POLITENESS_ENABLED=true`. Fetches and caches robots.txt, enforces Crawl-delay, blocks Disallow paths. 14 unit tests.
- **Proxy support — SCRAPER_PROXY_URL (#129, ADR-0020)** — contributed by @Jackal991. Single env var plumbed through httpx clients and Playwright browser context. Fail-open with WARN on proxy failure.

### 📊 Observability & Operations

- **Health probes and /metrics endpoint (#123, ADR-0018)** — `/health` returns per-dependency probe results. `/metrics` exports counters, histograms, and gauges in OpenMetrics format. Structured JSON logging with request_id correlation.
- **Health endpoint fix (#125)** — corrected `health` → `healthy` state attribute name.

### 🧹 Quality & Cache

- **Intelligent scrape cache — ETag/Last-Modified revalidation (#126, ADR-0019)** — the Valkey-backed cache now uses conditional GETs. 304 responses extend TTL without re-downloading. SHA-256 content hashing detects changes. Per-domain TTLs, configurable bounds, volatility-aware TTL adjustment.
- **Extraction QA pipeline (#111, ADR-0016)** — post-extraction quality gates for boilerplate detection, completeness checks, and block page detection.
- **Graceful degradation (#112)** — `smart_scrape()` degrades through tiers on low-quality content.
- **Structured metadata enrichment (#116, ADR-0004)** — extracts JSON-LD, OpenGraph, Twitter Card, and meta tags alongside markdown.
- **Artifact-pyramid CLI output (#141, ADR-0024)** — `--pyramid` flag on `agent` and `answer` commands.

### 🔧 Fixes & Polish

- **Async event loop unblocked (#166)** — synchronous `model.encode()` now runs via thread executor.
- **Qdrant API compatibility (#146, #165)** — uint64 point IDs, `using` keyword for qdrant-client 1.18.
- **Numpy dependency removed (#143)** — pure-Python dot product replaces numpy in semantic reranking.
- **Port exposure cleanup** — semantic-svc port no longer exposed to host.
- **Conditional auth header** — `Authorization: Bearer` only sent when `api_key` is non-empty.
- **ADR cleanup** — stale ADR-0020 removed, proxy ADR renumbered.

### Contributors

Special thanks to everyone who contributed to this release:

- **@Jackal991** — PR #129: `SCRAPER_PROXY_URL` env var with fail-open logic, proxy identity logging, and credential redaction. A clean, minimal implementation that fills a structural gap in the scrape pipeline.
- **@wysie** — PR #87: uv setup documentation for CLI dependencies. PR #86: portable Docker Compose config and SearXNG JSON API enabling.

And @magnus919 for the bulk of the work — 52 PRs across semantic search, web portal, answer endpoint, adapters, observability, caching, and quality infrastructure.

### Infrastructure

- **New Docker services**: `semantic-svc`, `qdrant`, `portal-svc`
- **New Qdrant volume**: `qdrant_data` for persistent vector storage
- **New environment variables**: `SCRAPER_PROXY_URL`, `GITHUB_TOKEN`, `SCRAPE_CACHE_DOMAIN_TTLS`, `SCRAPE_CACHE_MIN_TTL`, `SCRAPE_CACHE_MAX_TTL`, `SCRAPE_CACHE_VOLATILE_CAP`, `SCRAPE_CACHE_STABLE_MULTIPLIER`, `SCRAPER_POLITENESS_ENABLED`, `SCRAPER_POLITENESS_CRAWL_DELAY`, `SCRAPER_POLITENESS_ROBOTS_TTL`, `QA_MIN_QUALITY_THRESHOLD`, `NEAR_DUP_THRESHOLD`, `NEAR_DUP_MODE`, `SEMANTIC_URL`, `QDRANT_URL`, `VECTOR_INDEX_MAX_DOCS`
- **First-time startup note**: `docker compose up` will pull the Qdrant v1.18.2 image (~50MB). The Qdrant collection is created automatically on first index. Expect ~2-3 minutes for initial service readiness.

### Full PR List

PRs #83, #86–87, #89–90, #94, #96–97, #101–105, #111–118, #120–123, #125–126, #128–129, #132, #135–138, #140–143, #145–149, #156–168.

---

## [0.6.0] — 2026-06-05

### Added

- **Adapter framework** — pluggable site-specific content handlers with auto-registration, priority-sorted dispatch, and per-adapter fallback chains. See `docs/adr/0001`–`0009`.
- **YouTube adapter** — extracts full video transcripts and descriptions via `youtube_transcript_api` (free, no key). Returns YAML frontmatter (title, channel, views) + description + transcript as markdown.
- **Bluesky adapter** — extracts posts and threads via the AT Protocol public API (no auth required). Returns YAML frontmatter (author, handle, engagement stats) + post text with richtext facet conversion (mentions, links, tags) + depth-1 replies as markdown.
- **Barrier classification (Phase 1)** — `_classify_barrier()` replaces the boolean `_looks_suspicious()` heuristic. Detects Cloudflare, DDoS-Guard, CAPTCHA, rate-limit, Substack redirect, and empty-content barriers with confidence scoring. ADR-0015.
- **Valkey scrape result cache** — TTL-based cache (default: 1 hour) for scrape results. Configurable via `SCRAPE_CACHE_TTL` env var. Adapter results excluded from cache.
- **Search failure detection** — `SearchHealth` dataclass reports per-query engine status (total engines, responding engines, degraded vs empty-result signal).
- **Firecrawl v2 category translation** — `sources` and `categories` parameters on `/v2/search` are translated to SearXNG-native categories. `sources=news` → `categories=news`, `categories=research` → `categories=science`, etc. CLI exposes `--sources` and `--categories` flags.
- **Architecture-as-code** — C4 system-context and container diagrams in `docs/architecture.md`. GitHub Actions CI workflow validates ADR naming, required sections, and index freshness.
- **Architecture Decision Records (ADRs 0001–0015)** — covers adapter framework, scraper pipeline, stealth Playwright, webhooks, search architecture, binary content, barrier classification.

### Changed

- `smart_scrape()` now: (1) checks adapter registry, (2) checks Valkey cache, (3) runs barrier classification after each tier, (4) runs the existing 5-tier pipeline.
- `/v2/search` response routes results to the correct top-level key (`data.web`, `data.news`, `data.images`) based on the `sources` filter.
- `SearchRequest` model now accepts `sources: list[str] | None`.
- CLI search subcommand now accepts `--sources` (web, news, images, video, social) and `--categories` (research, github, pdf, etc.).

### Documentation

- Architecture Decision Records: 15 total (was 9).
- `docs/architecture.md` — C4 System Context and Container diagrams.
- `CONTRIBUTING.md` — ADR convention section.
- `AGENTS.md` — search parameters documentation for AI agents.
- `README.md` — Search endpoint docs with parameter and translation tables, detailed Adapters section (YouTube + Bluesky).
- `.env.sample` — added `SCRAPE_CACHE_TTL`, `ADAPTER_YOUTUBE_API_KEY`.

### Infrastructure

- `.github/workflows/architecture.yml` — CI pipeline validating ADR structure on push/PR to main.

## [0.5.0] — 2026-05-31

### Security

- **API key authentication** — Set `API_KEY` in `.env` to enable bearer token auth. All endpoints (except `/health`) require `Authorization: Bearer` or `X-API-Key`. When unset, a startup warning is logged, an `X-Security-Warning` header is added to every response, the `/health` endpoint includes a structured `security` field, and the CLI prints a one-time stderr warning. Backward compatible — existing deployments work unchanged. (#83)
- **Private IP / SSRF protection** — Both `browser-svc` and `scraper-svc` now validate destination URLs before navigation. RFC 1918 private ranges, loopback, link-local, cloud metadata endpoints (169.254.169.254), and Docker host suffixes (`.docker.internal`) are blocked with a 400 error. Hostnames are resolved to IPs and checked, preventing DNS rebinding attacks. (#83)
- **Port hardening** — Removed host port exposure from `browser-svc` (8012), `scraper-svc` (8001), and `parse-svc` (8013). These services are only reachable on Docker's internal DNS. The agent API on port 8080 remains the sole external entry point. (#83)

### Changed

- **Breaking**: `browser-svc`, `scraper-svc`, and `parse-svc` no longer publish host ports. Scripts or tools that connect directly to these services on ports 8012, 8001, or 8013 must be updated to go through the agent API on port 8080. (#83)
- **Breaking**: Existing `.env` files that manually set `SCRAPER_URL=http://localhost:8001` will break. Change to `http://scraper-svc:8001` (Docker internal DNS). (#83)
- `docker-compose.yml` restructured — internal services no longer expose ports. (#83)

### Added

- New `agent-svc/agent/auth.py` — centralized authentication module with `verify_api_key()` FastAPI dependency. (#83)
- `SECURITY.md` — security policy, supported versions, and disclosure acknowledgments. (#83)

### Credits

This release was prompted by a responsible disclosure from **Bertie**, who
privately reported the unauthenticated browser pivot vulnerability. Thank you.

## [0.4.0] — 2026-05-31

### Added

- **CLI subcommands for monitor, parse, and generate-llmstxt** (`groktocrawl` binary) — three new entry points for managing change monitors (create/list/get/update/delete), parsing document files (PDF, EPUB, DOCX) to markdown, and generating llms.txt for a website with async polling. (#79, #81)
- **Agent system prompt upgrade** (`agent-svc/agent/research.py`) — replaced the minimal 7-line `SYSTEM_PROMPT` with a comprehensive prompt that instructs the LLM to evaluate source quality, synthesize across multiple pages, detect contradictions, flag thin evidence, and cite sources by URL. The new prompt defines a clear source authority ladder (official docs > established news > blogs/forums) and tells the agent to be thorough and precise rather than just "concise."
- **Extract prompt upgrade** — `EXTRACT_SYSTEM_PROMPT` now instructs the LLM to extract ALL instances of requested data, flag missing/ambiguous values, and organize output clearly.
- **Model selection passthrough** — the `model` field from `POST /v2/agent` requests is now respected. When set to a specific model name (e.g., `"gpt-4o"`) it overrides the environment-configured default.
- **Domain metadata in context** — each scraped source now includes `(domain: example.com)` in the context passed to the LLM.

### Changed

- **Search results format** — `/v2/search` now returns results grouped by source type per Firecrawl v2 spec (`{"data": {"web": [...]}}`) instead of a flat array. (#66)

### Fixed

- **50x search speedup** — removed redundant per-result scraping from `/v2/search`. (#69)
- **Search CLI parsing** — `groktocrawl search` correctly reads from `data.web` dict instead of the flat `data` list. (#71)

## [0.3.0] — 2026-05-24

### Added

#### Substack Scraping (Stealth Playwright Config)

- **Stealth Playwright renderer** (`scraper-svc/scraper/stealth.py`) — the scraper-svc's Tier 3 now launches Chromium with `--disable-blink-features=AutomationControlled`, a real Chrome 131 User-Agent, 1920x1080 viewport, `en-US` locale, `America/New_York` timezone, and `navigator.webdriver` override via `add_init_script()`.
- **SPA content retry** — when extracted markdown is short (< 500 chars) or suspicious, the scraper scrolls to the bottom of the page and waits up to 6s to trigger lazy-loaded content.
- **Substack redirect detection** — `_is_substack_redirect()` detects `session-attribution-frame`, `channel-frame`, and GTM noscript redirects.
- **Content gate fix** — `smart_scrape()` now returns extracted content immediately when `_looks_suspicious()` passes, even if embedded content signals are present.

#### Cookie Persistence (scraper-svc)

- **Valkey-backed Cloudflare cookie store** — `cf_clearance` cookies are cached and reused across scrapes via Valkey (25-minute TTL, TLD+1 domain scoping).
- **Cookie injection before navigation** — `fetch_via_playwright()` injects stored `cf_clearance` cookies before navigating.

### Changed

- **Browser args stripped** — removed `--disable-web-security`, `--disable-features=IsolateOrigins,site-per-process`, and `--disable-features=BlockInsecurePrivateNetworkRequests` from the stealth config.

### Fixed

- **False-positive embedded content detection** — `substackcdn.com` was falsely matching the `cdn.` domain pattern. Fixed by prioritizing `content_good` over embedded content signals.

## [0.2.0] — 2026-05-24

### Added

#### Five-Tier Scrape Pipeline

- **Tier 3.5: FlareSolverr** — optional profile-gated container for hard Cloudflare challenges.
- **Tier 4: LLM-Assisted Recovery** — when standard tiers return suspicious content, the scraper calls a configured LLM to analyze the page.
- **Tier 5: LLM Cloudflare Classification** — when all bypass methods fail, the LLM explains the block type and suggests alternative access paths.

#### Binary Content Support

- **Content-Type detection** — auto-detects PDF, EPUB, images, and archives at the HTTP tier. Returns a structured `download` payload.
- **`groktocrawl download <url>`** — new CLI subcommand for binary content.

### Infrastructure

- **Contribution templates**: added bug report and feature request issue templates, PR template, updated `CONTRIBUTING.md`.

## [0.1.1] — 2026-05-24

### Added

- **Unified activity endpoint** (`GET /v2/activity`) — lists all active jobs across all job types.
- **Lightweight meta tag extraction** (`POST /scrape/meta`) — single HTTP GET to extract `<title>`, `<meta name="description">`, and `<meta property="og:description">`.
- **Sentence-boundary-aware description extraction** — llms.txt generator produces descriptions ending at complete sentence boundaries.

### Fixed

- CLI `active` command (no longer returns 404).
- llms.txt descriptions no longer truncated mid-sentence or include boilerplate.

## [0.1.0] — 2026-05-21

Initial release. Self-hosted, Firecrawl-compatible web scraping and AI research API.

- `/v2/scrape`, `/v2/crawl`, `/v2/batch/scrape`, `/v2/search`, `/v2/map`, `/v2/agent`, `/v2/extract`, `/v2/browser`, `/v2/monitor`, `/v2/parse`, `/v2/generate-llmstxt`
- CLI with all endpoint support
- Valkey-backed job store with 24h TTL
- Webhook delivery with HMAC signing and retry
- Docker Compose deployment
