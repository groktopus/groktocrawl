# Changelog

All notable changes to GroktoCrawl are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Politeness protocol — optional per-domain rate limiting with robots.txt respect** — new `scraper-svc/scraper/politeness.py` module. Gated behind `SCRAPER_POLITENESS_ENABLED=true` in Docker `.env`, off by default. When enabled: fetches and caches robots.txt per domain (Valkey-backed), enforces configurable `Crawl-delay` between requests, and blocks URLs matching `Disallow` paths. Politeness metadata returned in scrape response. Configurable via `SCRAPER_POLITENESS_CRAWL_DELAY` and `SCRAPER_POLITENESS_ROBOTS_TTL` env vars. See `.env.sample` for full documentation.
- **Unit tests for politeness module** — 14 tests covering robots.txt parsing, check/delay/block decision flow, rate limit timing, metadata reporting, and domain extraction.
- **`politeness` field in scrape response** — `ScrapeResponse.data.politeness` returned when `SCRAPER_POLITENESS_ENABLED=true`.
- **Graceful degradation** — `smart_scrape()` now checks content quality after each tier. When quality is below `QA_MIN_QUALITY_THRESHOLD` (default 0.3), the pipeline degrades to the next tier instead of returning low-quality content. Best-effort result is returned if all tiers produce low quality, with a `warning` field. Configurable via `QA_MIN_QUALITY_THRESHOLD` env var.
- **Extraction quality gates** — post-extraction content quality assessment for boilerplate detection, completeness checks, and block page detection. Three lightweight heuristic gates in `scraper-svc/scraper/extract.py` produce a composite quality score (0.0-1.0) with structured breakdown. Quality score is returned in scrape response metadata — non-blocking, consumers set their own tolerance. See ADR-0016.
- **`quality` field in scrape response** — `ScrapeResponse.data` and `ScrapeData.quality` now carry the quality assessment result.
- **Unit tests for quality gates** — 18 tests covering boilerplate detection, completeness checking, block page detection, and integrated quality assessment.
- **GitHub file adapter** (`scraper-svc/scraper/adapters/github.py`) — structured content extraction for raw.githubusercontent.com, blob URLs, repo roots (README + metadata), and tree listings. Uses raw.githubusercontent.com direct fetch as primary path with Contents API fallback. Extension allowlist for binary detection. Per-endpoint sliding window rate-limit tracker. Priority 200.
- **GitHub social adapter** (`scraper-svc/scraper/adapters/github_social.py`) — issues, pull requests, discussions, releases (single + list), and commits via GitHub GraphQL API (v4). Three-tier fallback chain per resource: GraphQL → REST API → HTML page scrape (readability-lxml + markdownify). Works without a token at 60 req/hr (REST fallback) or without any auth (HTML scrape). Priority 190.
- **`GITHUB_TOKEN` environment variable** — enables 5,000 API req/hr and GraphQL access for richer metadata (reviews, diff stats, threaded comments, discussion answers, release assets). `public_repo` scope for public repos, `repo` scope for private repos.
- **CI tests for GitHub adapters** — 5 integration tests (raw file, blob→raw rewrite, repo root README, tree listing, social issue fallback) in `tests/test_stack.py`.

### Changed

- Updated README with full GitHub adapter documentation covering 10 URL types and configuration.
- Updated `.env.sample` with `GITHUB_TOKEN` configuration guide.
- `smart_scrape()` now checks the adapter registry before the tier pipeline — matched adapters short-circuit to their own extraction.
- `smart_scrape()` calls `assess_quality()` after each successful tier, attaching the quality result to the response dict.
- `ScrapeData` model in agent-svc now includes an optional `quality` field.

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

### Added

- _Nothing yet._

## [0.5.0] — 2026-05-31

### Security

- **API key authentication** — Set `API_KEY` in `.env` to enable bearer token auth. All endpoints (except `/health`) require `Authorization: Bearer *** or `X-API-Key: ***`. When unset, a startup warning is logged, an `X-Security-Warning` header is added to every response, the `/health` endpoint includes a structured `security` field, and the CLI prints a one-time stderr warning. Backward compatible — existing deployments work unchanged. (#83)
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
- **SkillOpt Epoch 1 — skill document restructure** (`skills/groktocrawl/`) — added browser session lifecycle guidance, structured extraction examples, search backend config reference, and cross-command chaining patterns. (#73)
- **SkillOpt Epoch 2 — structured extraction workflow** — full session ID plumbing through multiple commands, browser session lifecycle reference, multi-step research workflow example with PDF source handling, and "When to use which" decision table. (#74)
- **Skill document optimization** — replaced sparse command list with comprehensive workflow-oriented references including domain exploration strategy, change monitoring, and error recovery patterns. (#72)
- **Agent system prompt upgrade** (`agent-svc/agent/research.py`) — replaced the minimal 7-line `SYSTEM_PROMPT` with a comprehensive prompt that instructs the LLM to evaluate source quality, synthesize across multiple pages, detect contradictions, flag thin evidence, and cite sources by URL. The new prompt defines a clear source authority ladder (official docs > established news > blogs/forums) and tells the agent to be thorough and precise rather than just "concise."
- **Extract prompt upgrade** — `EXTRACT_SYSTEM_PROMPT` now instructs the LLM to extract ALL instances of requested data, flag missing/ambiguous values, and organize output clearly.
- **Model selection passthrough** — the `model` field from `POST /v2/agent` requests is now respected. When set to a specific model name (e.g., `"gpt-4o"`) it overrides the environment-configured default. When omitted or `"default"`, behavior is unchanged. Files changed: `api.py`, `worker.py`, `research.py`.
- **Domain metadata in context** — each scraped source now includes `(domain: example.com)` in the context passed to the LLM, giving the research agent signal for credibility evaluation without adding a maintenance-heavy classification system.

### Changed

- **Search results format** — `/v2/search` now returns results grouped by source type per Firecrawl v2 spec (`{"data": {"web": [...]}}`) instead of a flat array. (#66)

### Fixed

- **50x search speedup** — removed redundant per-result scraping from `/v2/search`. Previously, every search result was independently scraped in addition to the search itself. Now results are returned directly without post-processing. (#69)
- **Search CLI parsing** — `groktocrawl search` correctly reads from `data.web` dict instead of the flat `data` list, fixing silent empty-result returns on old CLI versions. (#71)
- **SkillOpt Epoch 3 — structured data extraction guidance** — documented `extract` command prompt tips, error recovery patterns (JS-rendered pages, broad prompts, auth walls), and when to use `extract` vs `browser + executeScript`. (#75)
- **SkillOpt Epoch 4 — multi-source research fallback chain** — documented systematic escalation from search → scrape → browser → agent, including when-to-escalate thresholds (500-char scrape, 403/blocked, conflicting info). (#76)
- **SkillOpt Epoch 5 — change monitoring documentation** — added monitor lifecycle guidance, active job tracking distinction (monitors vs crawl/agent jobs), and Valkey storage details. (#77)
- **Monitor docs correction** — clarified that monitor management uses the REST API (POST/PATCH/DELETE /v2/monitor), not CLI subcommands. (#78)

## [0.3.0] — 2026-05-24

### Added

#### Substack Scraping (Stealth Playwright Config)

- **Stealth Playwright renderer** (`scraper-svc/scraper/stealth.py`) — the scraper-svc's Tier 3 now launches Chromium with `--disable-blink-features=AutomationControlled`, a real Chrome 131 User-Agent, 1920x1080 viewport, `en-US` locale, `America/New_York` timezone, and `navigator.webdriver` override via `add_init_script()`. Matches the browser-svc's proven configuration exactly.

- **SPA content retry** — when extracted markdown is short (< 500 chars) or suspicious, the scraper scrolls to the bottom of the page and waits up to 6s to trigger lazy-loaded or dynamically-injected content from JS-rendered pages.

- **Substack redirect detection** — `_is_substack_redirect()` detects `session-attribution-frame`, `channel-frame`, and GTM noscript redirects with a 5-second wait for delayed resolution.

- **`networkidle` timeout** — increased to 45s with no `domcontentloaded` fallback, matching the browser-svc pattern that handles Substack's persistent analytics connections.

- **Content gate fix** — `smart_scrape()` now returns extracted content immediately when `_looks_suspicious()` passes, even if embedded content signals (iframes for comments/analytics) are present. Previously, `substackcdn.com` was falsely matching the `cdn.` domain pattern in `EMBEDDED_CONTENT_DOMAINS`, causing 10K+ char articles to be discarded.

- **Browser-svc fallback** (`_fetch_via_browser_svc()`) — when Substack redirects are detected and the content gate can't resolve them, the scraper can create a browser-svc session, navigate, and extract article text via `executeScript` with `document.querySelector('article').innerText`.

#### Cookie Persistence (scraper-svc)

- **Valkey-backed Cloudflare cookie store** (`scraper-svc/scraper/cookie_store.py`) — `cf_clearance` cookies are cached and reused across scrapes via the shared Valkey instance. Cross-service sharing: cookies solved by the browser-svc are immediately available to the scraper-svc (25-minute TTL, TLD+1 domain scoping).

- **Cookie injection before navigation** — `fetch_via_playwright()` injects stored `cf_clearance` cookies before navigating, skipping Cloudflare challenges for previously-solved domains.

- **Cookie storage after successful scrape** — new `cf_clearance` cookies are persisted to Valkey for future scrapes.

- **Graceful degradation** — if Valkey is unavailable, the scraper continues without cookie persistence (logs a warning, returns content normally).

### Changed

- **Browser args stripped to match browser-svc**: removed `--disable-web-security`, `--disable-features=IsolateOrigins,site-per-process`, and `--disable-features=BlockInsecurePrivateNetworkRequests` from the stealth config. These extra flags deviated from real browser behavior and were potentially detectable.

### Fixed

- **False-positive embedded content detection**: `_has_embedded_content()` was matching `substackcdn.com` against the `cdn.` domain pattern in `EMBEDDED_CONTENT_DOMAINS`, causing all Substack pages with comment/analytics iframes to be flagged as embedded-document portals and sent through the recovery chain. Fixed by prioritizing `content_good` over embedded content signals.

## [0.2.0] — 2026-05-24

### Added

#### Five-Tier Scrape Pipeline

Complete overhaul of the scraper from a fixed three-tier system to an adaptive five-tier pipeline:

- **Tier 3.5: FlareSolverr** — optional profile-gated container for hard Cloudflare challenges (CAPTCHA, strict fingerprinting). Enable with `docker compose --profile flare-solverr up`.
- **Tier 4: LLM-Assisted Recovery** — when standard tiers return suspicious content (Cloudflare challenges, error pages), the scraper calls a configured LLM to analyze the page. The LLM can extract iframe URLs and retry the scrape on the real content URL, return extracted text embedded in the page, or identify bot challenge types.
- **Tier 5: LLM Cloudflare Classification** — when all bypass methods fail, the LLM explains the block type (CAPTCHA, JS challenge, rate limit) and suggests alternative access paths (Wayback Machine, Google Cache), turning a hard failure into actionable information.

#### Binary Content Support

- **Content-Type detection** (`scraper-svc`) — auto-detects PDF, EPUB, images, and archives at the HTTP tier. Returns a structured `download` payload (filename, size, content_type) alongside markdown instead of failing.
- **`groktocrawl download <url>`** — new CLI subcommand that fetches binary content directly via HTTP with a real Chrome User-Agent. Supports `--extract-text` for PDF/EPUB text extraction (requires optional `pymupdf`/`ebooklib` deps). Auto-derives filenames from URL or Content-Type.

#### Iframe Content Detection

- **`_has_embedded_content()`** — detects when a Playwright-rendered page contains an `<iframe>`, `<embed>`, or `<object>` pointing to document URLs (PDFs, EPUBs, known document-serving domains like Sci-Hub, Academia, ResearchGate). Pages with embedded content are escalated to LLM recovery for URL extraction instead of returning the portal page text.

#### Cloudflare Bypass

- **Stealth browser config** — browser-svc now launches Playwright with real Chrome User-Agent, `--disable-blink-features=AutomationControlled`, `navigator.webdriver` override, realistic viewport/locale/timezone, and `networkidle` wait strategy. Cloudflare challenge pages are detected and given an 8-second resolution window.
- **Cookie persistence** — `cf_clearance` cookies are cached in Valkey with a 25-minute TTL. Subsequent scrapes to the same domain skip the Cloudflare challenge.
- **FlareSolverr sidecar** — profile-gated container (`profiles: [flare-solverr]`) for sites with aggressive Cloudflare protection.

#### DDoS-Guard Detection

- **Browser-svc**: DDoS-Guard JS challenge detection alongside Cloudflare — title checks for "DDoS-Guard", URL checks for `/.well-known/ddos-guard/`.
- **Scraper-svc bot challenge detection**: `fetch_via_playwright()` now uses `networkidle` wait and checks for bot challenges post-navigation, with an 8-second resolution window.
- **Stealth scraper-svc Playwright config** — the scraper-svc's Tier 3 renderer now uses the same stealth configuration as browser-svc: `--disable-blink-features=AutomationControlled`, real Chrome 131 User-Agent, 1920x1080 viewport, `en-US` locale, `America/New_York` timezone, and `navigator.webdriver` override via `add_init_script()`. Additional fingerprint hardening: `navigator.plugins` array population, `navigator.languages` override, `window.chrome` presence, and WebGL vendor/renderer spoofing. See `scraper-svc/scraper/stealth.py`.
- **`networkidle` → `domcontentloaded` timeout fallback** — when `networkidle` exceeds 30s (caused by persistent analytics connections on sites like Substack), the scraper falls back to `domcontentloaded` and proceeds with the rendered content instead of failing.
- **Substack session-frame redirect detection** — `_is_substack_redirect()` detects when Substack injects `session-attribution-frame`, `channel-frame`, or GTM noscript redirects. Detection integrated into `fetch_via_playwright()` with a 5-second wait for delayed resolution, and into `_looks_suspicious()` for LLM recovery triggering.
- **Bot challenge re-check** — after the 8-second resolution window, the scraper re-verifies the page title/URL before proceeding, avoiding false positives from brief challenge page flashes.

#### Cookie Persistence (scraper-svc)

- **Valkey-backed Cloudflare cookie store** — the scraper-svc's Playwright renderer now caches and reuses `cf_clearance` cookies via the shared Valkey instance (`scraper-svc/scraper/cookie_store.py`). Cookies are stored with a 25-minute TTL, scoped to TLD+1 domain. Cross-service sharing: cookies solved by the browser-svc are immediately available to the scraper-svc, and vice versa.
- **Cookie injection before navigation** — `fetch_via_playwright()` injects stored `cf_clearance` cookies into the browser context before navigating, potentially skipping Cloudflare challenges entirely for previously-solved domains.
- **Cookie storage after successful scrape** — after extracting content, any new `cf_clearance` cookies are persisted to Valkey for future scrapes.
- **Graceful degradation** — if Valkey is unavailable, the scraper continues without cookie persistence (logs a warning, returns the content normally).

#### LLM Fixture

- **Prompt-aware fixture** — the `llm-svc` fixture now handles recovery prompt schemas: extracts `<iframe src>` URLs from page content when the prompt mentions `iframe_url` or `recovery`, returns Cloudflare/DDoS-Guard classification data when the prompt mentions `cloudflare` or `block_type`. Makes the full pipeline demonstrable with `docker compose --profile fixture up` (no external API key needed).

### Changed

- **Real Chrome User-Agent everywhere**: scraper-svc's `smart_scrape()` httpx client now uses a real Chrome 131 User-Agent instead of the custom `GroktoCrawl/0.1` string that triggered Cloudflare.
- **`LLM_BASE_URL` configured in docker-compose**: scraper-svc now has `LLM_BASE_URL=http://llm-svc:8011/v1` in its environment block, fixing the port mismatch with the llm-svc fixture.
- **Recovery prompt content**: removed 4000-char truncation — the LLM recovery module now sends the full page content instead of the first 4000 characters. URL fragments (`#view=FitH`) are stripped from extracted iframe URLs before retrying.

### Fixed

- **Recovery received markdown, not raw HTML** — the LLM recovery module was receiving the markdown-converted page text (no HTML tags) instead of the raw HTML. Iframe URLs in the HTML were invisible to the LLM. Fixed by passing `raw_html_start` when available.
- **Duplicate `app = FastAPI(...)`** line in browser-svc left from a previous merge conflict resolution.
- **LLM fixture returned wrong JSON schema** — the fixture was returning `{"result": "structured response"}` which didn't match the recovery module's expected `{"action": "iframe_url", "url": "..."}` schema.

### Infrastructure

- **Contribution templates**: added bug report and feature request issue templates (`.github/ISSUE_TEMPLATE/`), PR template (`.github/PULL_REQUEST_TEMPLATE.md`), and updated `CONTRIBUTING.md` with Conventional Commits, DCO sign-off requirements, and PR template reference.

## [0.1.1] — 2026-05-24

### Added

- **Unified activity endpoint** (`GET /v2/activity`) — lists all active/processing jobs across all job types (crawl, agent, extract, batch_scrape, llmstxt). The CLI `active` command was previously broken (hit a route that was never implemented).
  - `store.py`: `list_active_jobs()` method using Valkey SCAN + pipeline batch fetch
  - New Pydantic models: `ActivityItem`, `ActivityResponse`
  - CLI: `groktocrawl active` now shows job kind and URL columns; JSON output key is `active_jobs`

- **Lightweight meta tag extraction** (`POST /scrape/meta`) — new endpoint on the scraper service that does a single HTTP GET and extracts `<title>`, `<meta name="description">`, and `<meta property="og:description">` from raw HTML. No Playwright, no readability, no markdown conversion.
  - New module: `scraper-svc/scraper/meta.py`
  - New Pydantic model: `MetaResponse`

- **Sentence-boundary-aware description extraction** — the llms.txt generator now produces descriptions that end at complete sentence boundaries instead of hard-char truncation.
  - Boilerplate signal filtering (30+ signal words: cookie, nav, footer, etc.)
  - Lines under 30 chars filtered out
  - Short candidates joined to reach ~100 char minimum
  - Sentence-boundary regex scan (`. `, `! `, `? `) instead of `[:150]`
  - Ellipsis fallback when no boundary found

- **Meta tag integration** — llms.txt generation tries the meta endpoint first (cheap, one GET); falls through to full scrape + sentence-boundary extraction only when meta tags are absent or under 40 chars.

### Testing

- 8 unit tests for `_extract_description()` pure function (no Docker)
- 4 new integration tests: meta endpoint, fallback, sentence-boundary, meta preference
- 3 new fixture pages on the test-site (`/content/multi-sentence`, `/content/with-meta`, `/content/with-boilerplate`)

### Fixed

- CLI `active` command (no longer returns 404 — now hits `GET /v2/activity`)
- llms.txt descriptions no longer truncated mid-sentence
- llms.txt descriptions no longer include cookie/nav boilerplate

## [0.1.0] — 2026-05-21

Initial release. Self-hosted, Firecrawl-compatible web scraping and AI research API.

### Features

- `/v2/scrape` — Single URL to clean markdown via three-tier scraper (llms.txt → Accept: text/markdown → Playwright)
- `/v2/crawl` — Recursive site crawling with depth and page limits
- `/v2/batch/scrape` — Batch scrape multiple URLs
- `/v2/search` — Web search via SearXNG with automatic content scraping
- `/v2/map` — URL discovery on a site with optional search filter
- `/v2/agent` — Autonomous research agent (search → scrape → LLM synthesis)
- `/v2/extract` — Structured data extraction from URLs with JSON Schema support
- `/v2/browser` — Headless browser sessions (navigate, click, type, screenshot, scroll, executeScript)
- `/v2/monitor` — Scheduled change detection with cron-based checking and webhook delivery
- `/v2/parse` — Document file parsing (PDF, DOCX, PPTX, XLSX) to markdown
- `/v2/generate-llmstxt` — Generate llms.txt files for any website
- CLI with all endpoint support
- Valkey-backed job store with 24h TTL
- Webhook delivery for all async endpoints with HMAC signing and retry
- Docker Compose deployment
