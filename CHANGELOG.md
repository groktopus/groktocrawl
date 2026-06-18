# Changelog

All notable changes to GroktoCrawl are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0](https://github.com/groktopus/groktocrawl/compare/v0.7.0...v0.8.0) (2026-06-18)


### Features

* 10 security scraper adapters — threat intelligence API coverage ([c37637e](https://github.com/groktopus/groktocrawl/commit/c37637eb7d8b518485d4159d26e7ac9ccc15dd3d))
* add .dockerignore to reduce Docker build context size ([#254](https://github.com/groktopus/groktocrawl/issues/254)) ([eb48825](https://github.com/groktopus/groktocrawl/commit/eb48825497f5a7a428fc326cfb9b39b341512603)), closes [#241](https://github.com/groktopus/groktocrawl/issues/241)
* add 10 security scraper adapters for threat intelligence APIs ([4330b16](https://github.com/groktopus/groktocrawl/commit/4330b166ddbbbefca857bb40b2087cd18ee95cd0))
* add DEBUG-level logging to scraper adapter fallback chains ([#251](https://github.com/groktopus/groktocrawl/issues/251)) ([f12706d](https://github.com/groktopus/groktocrawl/commit/f12706dacd47096cbf1bab6d905f14d58418f6b8)), closes [#239](https://github.com/groktopus/groktocrawl/issues/239)
* add DNS resolution failure logging to SSRF guard ([#253](https://github.com/groktopus/groktocrawl/issues/253)) ([080ef60](https://github.com/groktopus/groktocrawl/commit/080ef603a5f2ec3205662d397f080a9c8a16e4e1))
* add graceful shutdown handling for fire-and-forget async tasks ([#231](https://github.com/groktopus/groktocrawl/issues/231)) ([360b526](https://github.com/groktopus/groktocrawl/commit/360b52616055295da5e042bbf920ae8320cc6272))
* add Greenhouse and AshbyHQ ATS adapters ([#216](https://github.com/groktopus/groktocrawl/issues/216)) ([777fbd3](https://github.com/groktopus/groktocrawl/commit/777fbd387e8d20b2ccbc47b12fa4639d2f3765b7))
* add lifespan-based model loading and startup readiness to semantic-svc ([#223](https://github.com/groktopus/groktocrawl/issues/223)) ([5b98caf](https://github.com/groktopus/groktocrawl/commit/5b98caff097ea9cbbd25d4712b7923759d21bfc6))
* add portal-svc health probe to agent-svc ([#218](https://github.com/groktopus/groktocrawl/issues/218)) ([429335c](https://github.com/groktopus/groktocrawl/commit/429335ceffa007bdeb3672a9d3109aa97a55747c))
* add Query Intelligence — LLM-powered research planning (Phase 0) ([865da0d](https://github.com/groktopus/groktocrawl/commit/865da0db27906ce43d7b3e83c241f05a9ae7c42c))
* add search volume controls to agent-svc ([#214](https://github.com/groktopus/groktocrawl/issues/214)) ([064628d](https://github.com/groktopus/groktocrawl/commit/064628d41b24aba9ca3f8874dbe8ef08e85eb293))
* add Vaco (Highspring) adapter for jobs.vaco.com ([#222](https://github.com/groktopus/groktocrawl/issues/222)) ([8a5b54e](https://github.com/groktopus/groktocrawl/commit/8a5b54ecd3eb47fd20847a29437784266e171809))
* agent readiness level-up — observability, type checking, CI tooling, tests ([#267](https://github.com/groktopus/groktocrawl/issues/267)) ([3dbdd82](https://github.com/groktopus/groktocrawl/commit/3dbdd82f5ee4711c2005212345a96308b60976d3))
* NVD and CVE Program adapters — structured CVE data extraction ([b327921](https://github.com/groktopus/groktocrawl/commit/b32792105c2128c36bc352b111726b14da759a66))
* NVD and CVE Program adapters for structured CVE data extraction ([4e10527](https://github.com/groktopus/groktocrawl/commit/4e10527842984d48d189a33ce0c2cf308e21d9c1))
* pre-tier HEAD probe to detect bot protection before scrape pipeline ([#276](https://github.com/groktopus/groktocrawl/issues/276)) ([bce7edc](https://github.com/groktopus/groktocrawl/commit/bce7edcc7ae29cc2bf7064fe30b921f1368d73d9)), closes [#272](https://github.com/groktopus/groktocrawl/issues/272)
* Project Gutenberg adapter — chapter-structured book extraction ([#182](https://github.com/groktopus/groktocrawl/issues/182)) ([94a32d0](https://github.com/groktopus/groktocrawl/commit/94a32d0ca23e41b8fb6982081604e29006d9fd19)), closes [#181](https://github.com/groktopus/groktocrawl/issues/181)
* Query Intelligence — LLM-powered research planning (Phase 0) ([e7f87da](https://github.com/groktopus/groktocrawl/commit/e7f87da5b956cde382df66ef27bac79b89ead131))
* replace SearXNG with SlopSearX in Docker stack ([#204](https://github.com/groktopus/groktocrawl/issues/204)) ([d384355](https://github.com/groktopus/groktocrawl/commit/d3843553916ccdadf5c1065b0c8d585f1927bb50))
* Shopify adapter — bypass UCP content-negotiation trap ([04df892](https://github.com/groktopus/groktocrawl/commit/04df892cceef4280f032262ceb84b7f4bb1eec6f))
* Shopify adapter — bypass UCP content-negotiation trap ([f7852cf](https://github.com/groktopus/groktocrawl/commit/f7852cf206aa2a7e4042e625fd89e8797c35645e))
* standardize error handling across all API endpoints ([#208](https://github.com/groktopus/groktocrawl/issues/208)) ([5b1154d](https://github.com/groktopus/groktocrawl/commit/5b1154d3e2a607979f48a81f1f36f93abde2a007)), closes [#185](https://github.com/groktopus/groktocrawl/issues/185)


### Bug Fixes

* add from __future__ import annotations for Python 3.9 compat ([c8b8477](https://github.com/groktopus/groktocrawl/commit/c8b8477c85da6e4bfea1fb2be108cf553021c51c))
* add from __future__ import annotations for Python 3.9 compat ([a3cf2a8](https://github.com/groktopus/groktocrawl/commit/a3cf2a86d787161bcfe5e7191802cec7e6622306))
* add LLM health check and SSE status heartbeat to agent endpoint ([#265](https://github.com/groktopus/groktocrawl/issues/265)) ([e3df9b3](https://github.com/groktopus/groktocrawl/commit/e3df9b3ee3752d7e3176971fae3eb8531e301121))
* add missing Qdrant lookup logging to batch index endpoint ([#257](https://github.com/groktopus/groktocrawl/issues/257)) ([209f964](https://github.com/groktopus/groktocrawl/commit/209f9648d5b731ac73f113f9256a21aded1e253d))
* allow dependabot PRs to pass CI ([#230](https://github.com/groktopus/groktocrawl/issues/230)) ([ec4ab1d](https://github.com/groktopus/groktocrawl/commit/ec4ab1d52b9f4f9d66f084dbbd0c023d50ad3e62))
* correct SlopSearX image tag — CI strips v prefix ([#205](https://github.com/groktopus/groktocrawl/issues/205)) ([3691023](https://github.com/groktopus/groktocrawl/commit/3691023a19a10de9257f2edf9c56c3ac654209e5))
* deduplicate SSRF guard across services ([#235](https://github.com/groktopus/groktocrawl/issues/235)) ([dfa36fd](https://github.com/groktopus/groktocrawl/commit/dfa36fd1fbcd9f57b1190f60272cc3e491488835))
* deduplicate SSRF guard across services, consolidate on common.url.is_private_host ([dfa36fd](https://github.com/groktopus/groktocrawl/commit/dfa36fd1fbcd9f57b1190f60272cc3e491488835))
* deduplicate SSRF guard across services, consolidate on common.url.is_private_host ([cab348f](https://github.com/groktopus/groktocrawl/commit/cab348f6ec19f43dd516c8c43b01c80f555958c2)), closes [#195](https://github.com/groktopus/groktocrawl/issues/195)
* deprioritize video platform URLs in agent research pipeline ([#184](https://github.com/groktopus/groktocrawl/issues/184)) ([669c51b](https://github.com/groktopus/groktocrawl/commit/669c51b984a4e4965314751a45d35d4d01fee338))
* enable rich search for vector and hybrid_vector retrieval modes ([#234](https://github.com/groktopus/groktocrawl/issues/234)) ([43a8a16](https://github.com/groktopus/groktocrawl/commit/43a8a16ce44e8270c4f24e54d5fae12030694512))
* handle read-only settings.yml mount in searxng entrypoint ([40495f9](https://github.com/groktopus/groktocrawl/commit/40495f935d8f9df589f95639b60bade8055ff035))
* harden Playwright stealth configuration for anti-bot bypass ([#277](https://github.com/groktopus/groktocrawl/issues/277)) ([2794051](https://github.com/groktopus/groktocrawl/commit/279405170dea61caad5e26164b7656a63b2314af)), closes [#273](https://github.com/groktopus/groktocrawl/issues/273)
* increase agent prompt max_length from 10000 to 100000 ([#264](https://github.com/groktopus/groktocrawl/issues/264)) ([d088574](https://github.com/groktopus/groktocrawl/commit/d08857455ecbf6b40e1ec73c237f041b0577c19f))
* install system deps for Playwright Chromium Dockerfiles ([#259](https://github.com/groktopus/groktocrawl/issues/259)) ([d03995e](https://github.com/groktopus/groktocrawl/commit/d03995e994de8191b1370d8d9c71307323977261))
* lift FlareSolverr tier out of if result: gating ([#275](https://github.com/groktopus/groktocrawl/issues/275)) ([d74aac6](https://github.com/groktopus/groktocrawl/commit/d74aac647c39215936a0ddad933a9385c5f85bb6)), closes [#274](https://github.com/groktopus/groktocrawl/issues/274)
* log Qdrant lookup failures instead of silently swallowing them ([#248](https://github.com/groktopus/groktocrawl/issues/248)) ([f862b45](https://github.com/groktopus/groktocrawl/commit/f862b45a18f6d51b10eefbeb24bce4e2ae7a5c89)), closes [#237](https://github.com/groktopus/groktocrawl/issues/237)
* log semantic indexing failures instead of silently swallowing them ([#249](https://github.com/groktopus/groktocrawl/issues/249)) ([44b166a](https://github.com/groktopus/groktocrawl/commit/44b166a58e6b88294c80ea6b22d10f706dc829f8)), closes [#238](https://github.com/groktopus/groktocrawl/issues/238)
* narrow exception handling in semantic-svc model loading ([#252](https://github.com/groktopus/groktocrawl/issues/252)) ([28d0e05](https://github.com/groktopus/groktocrawl/commit/28d0e05c0e98b7686ed0c39a498dd6ff11972af1)), closes [#243](https://github.com/groktopus/groktocrawl/issues/243)
* only send enable_thinking param when explicitly opted in ([c97170d](https://github.com/groktopus/groktocrawl/commit/c97170d94d595b0afe220af5ba5b4ad72d2a0025))
* pass HF_TOKEN as Docker build arg for model downloads ([43ad4e0](https://github.com/groktopus/groktocrawl/commit/43ad4e068b143a4007abd19eac8954a95a638827))
* pin Qdrant image and cap memory to prevent OOM ([483f148](https://github.com/groktopus/groktocrawl/commit/483f148d004355d4f96e164e36dfb773ce3e63f7))
* print agent errors to stdout instead of stderr ([#266](https://github.com/groktopus/groktocrawl/issues/266)) ([198a41d](https://github.com/groktopus/groktocrawl/commit/198a41dcc414c4812786da1172a7bbf59c6a07da)), closes [#263](https://github.com/groktopus/groktocrawl/issues/263)
* read API key from API_KEY or GROKTOCRAWL_API_KEY env vars ([d5080ee](https://github.com/groktopus/groktocrawl/commit/d5080ee49efba0ac8152175becf9fcacae31318b))
* remove dead try/except in _compute_domain_category ([54c6f1e](https://github.com/groktopus/groktocrawl/commit/54c6f1efc43b009a2ae005f54b5a56e008c0c755))
* remove duplicate /v1 path in FlareSolverr URL construction ([#284](https://github.com/groktopus/groktocrawl/issues/284)) ([2656b65](https://github.com/groktopus/groktocrawl/commit/2656b656b6d536475a325f3979b8818ce117ca3a)), closes [#283](https://github.com/groktopus/groktocrawl/issues/283)
* remove unused RQ queue code ([#233](https://github.com/groktopus/groktocrawl/issues/233)) ([11663bd](https://github.com/groktopus/groktocrawl/commit/11663bd79d8a20d9c90953bda3dafbfd21fab489)), closes [#196](https://github.com/groktopus/groktocrawl/issues/196)
* rename unused variable to silence vulture dead-code check ([e7253cb](https://github.com/groktopus/groktocrawl/commit/e7253cbab16ad27e185e8b3eeb5da365f007a016))
* replace debug print() with structured logger in monitor.py ([#250](https://github.com/groktopus/groktocrawl/issues/250)) ([fda98a7](https://github.com/groktopus/groktocrawl/commit/fda98a763a7f25d05105036241bfbff278fb44e0)), closes [#242](https://github.com/groktopus/groktocrawl/issues/242)
* send API key as Bearer token in CLI requests ([d0221ae](https://github.com/groktopus/groktocrawl/commit/d0221ae9c047f5b56f16d412ca6914e20c09ccd0))
* send API key as Bearer token in CLI requests ([a037388](https://github.com/groktopus/groktocrawl/commit/a0373889ee3d02cce222de598ae7cac6afd4e132)), closes [#177](https://github.com/groktopus/groktocrawl/issues/177)
* stop firing real searches in agent-svc health check ([2e18603](https://github.com/groktopus/groktocrawl/commit/2e186036602d44d1dae377a19d6243bab730fd33))
* stop firing real searches in agent-svc health check ([#217](https://github.com/groktopus/groktocrawl/issues/217)) ([46bc439](https://github.com/groktopus/groktocrawl/commit/46bc439a44ba4f4d7cfb489ce97f9d3790779d22))
* tune code-quality CI thresholds and add setuptools config ([12226c8](https://github.com/groktopus/groktocrawl/commit/12226c861d34e2bb71b30e64aeabeffd05b3ef20))
* tune code-quality CI thresholds and add setuptools config ([#269](https://github.com/groktopus/groktocrawl/issues/269)) ([6af7293](https://github.com/groktopus/groktocrawl/commit/6af72932fdd53dbd42db1522411232e37bdf958c))


### Documentation

* add Shopify adapter to README ([154fb77](https://github.com/groktopus/groktocrawl/commit/154fb7780f82ac55c84c90c890bcbc16e84e5a25))
* add Shopify to AGENTS.md adapter listing ([fa7d701](https://github.com/groktopus/groktocrawl/commit/fa7d7015a746f21fdb2e292abb9a9b904bafc50d))
* bring Firecrawl comparison table current with v0.7.0 features ([e7e0ccb](https://github.com/groktopus/groktocrawl/commit/e7e0ccbe63b50c07d0932988154d81f57fc13ddd))
* document full adapter architecture — 20 adapters across 4 categories ([44a0d5d](https://github.com/groktopus/groktocrawl/commit/44a0d5d7e8afc402a8e8289b15347fdf466644c6))
* document full adapter architecture — 20 adapters across 4 categories ([0d892e2](https://github.com/groktopus/groktocrawl/commit/0d892e20c2e167c96aa66dfa1be76bf017a5b0f4)), closes [#169](https://github.com/groktopus/groktocrawl/issues/169)
* fix Firecrawl comparison — use — for unverified, correct self-hosted status ([9797731](https://github.com/groktopus/groktocrawl/commit/97977316bd4a5ede1a9ebf27d61002826e55cffa))
* only mark self-hosted rows I actually verified — rest — ([c032c7a](https://github.com/groktopus/groktocrawl/commit/c032c7a900555401e7daf652d6be033df80ea6b9))
* promote ADR-0031 from proposed to accepted ([#201](https://github.com/groktopus/groktocrawl/issues/201)) ([e992921](https://github.com/groktopus/groktocrawl/commit/e99292187885097769b6db4c4004d48212a3c1af))
* promote ADR-0032 from proposed to accepted ([#209](https://github.com/groktopus/groktocrawl/issues/209)) ([9a61aa5](https://github.com/groktopus/groktocrawl/commit/9a61aa55cc2344f2a4fc7ffd8c70283ace5dab27))
* promote ADR-0033 to accepted ([#215](https://github.com/groktopus/groktocrawl/issues/215)) ([fde96f4](https://github.com/groktopus/groktocrawl/commit/fde96f4e9b7767474e9413c5ec80952fdfde348e))
* promote ADR-0034 from proposed to accepted ([#229](https://github.com/groktopus/groktocrawl/issues/229)) ([6f56a14](https://github.com/groktopus/groktocrawl/commit/6f56a146eab8947270f2b47b7dabab664dab75c5))
* promote ADR-0035 from proposed to accepted ([#232](https://github.com/groktopus/groktocrawl/issues/232)) ([6a83d5d](https://github.com/groktopus/groktocrawl/commit/6a83d5db039bd2823a6e069c2bd8418feffcdc2a))
* replace — with ❌ in comparison table for consistency ([ef99a8f](https://github.com/groktopus/groktocrawl/commit/ef99a8fffca81867ad1fd513d728594baa90f32d))
* simplify Self-contained Docker row to emoji-only ([0f46b0e](https://github.com/groktopus/groktocrawl/commit/0f46b0e76e36bfc0d17ab776bdce3f766388e14e))
* update AGENTS.md with current adapter categories (20 total) ([41a9407](https://github.com/groktopus/groktocrawl/commit/41a94073d542ba030ae944c4a9e81e2e84150454))
* update README and CONTRIBUTING for current architecture ([dbe5a0a](https://github.com/groktopus/groktocrawl/commit/dbe5a0a5d4c441c0da14d6c3f719863f2ee45e30))

## [Unreleased]

### Changed

- **Split `scraper-svc/scraper/fetch.py` (1751 lines → 25 lines)** — extracted 5 focused modules: `cache.py` (Valkey cache client + freshness revalidation), `proxy.py` (httpx + Playwright proxy config), `dns_guard.py` (DNS rebinding + SSRF protection), `barrier.py` (bot challenge detection), and `fetch_strategy.py` (three-tier fetch pipeline). `fetch.py` is now a thin re-export. Updated `politeness.py` and `recovery.py` imports. No behavioral changes, no new dependencies. (closes #188)

- **Consolidated urlparse imports into shared `common/url.py`** — new module with `normalize_url()`, `extract_domain()`, `is_same_origin()`, and `is_private_host()`. Refactored 17+ inline `urlparse` call sites across agent-svc, scraper-svc, and browser-svc. Pure refactor — no behavior changes. Dockerfiles updated with `COPY common/ common/`. 28 new unit tests. (closes #194)

### Added

- **Greenhouse and AshbyHQ ATS adapters** — adds two ATS (Applicant Tracking System) adapters for structured job listing extraction. Greenhouse routes through the `boards-api.greenhouse.io` REST API with readability-lxml + markdownify content conversion. AshbyHQ extracts job data from SSR-embedded `window.__appData` JSON — no API calls needed. Both follow the existing `SiteAdapter` + `@adapter` decorator pattern. See `scraper-svc/scraper/adapters/greenhouse.py` and `scraper-svc/scraper/adapters/ashbyhq.py`. (closes #206, closes #207)

- **Search volume controls for agent-svc (ADR-0033)** — two independent mechanisms to prevent runaway Brave API consumption: (1) per-request max-searches cap (`AGENT_MAX_SEARCHES_PER_REQUEST`, default 5) enforced inside `SearXNGClient` before each search call, raises `RateLimitedError` (429) when exceeded; (2) per-client sliding-window rate limit (`AGENT_SEARCH_RATE_LIMIT`, default `10/60s`) using Valkey `INCR`/`EXPIRE`. New `X-Search-Budget` and `X-Search-Rate-Remaining` response headers. Search volume observable via new `search_calls_total` metrics counter. Backward compatible — existing callers see 429s only if they exceed limits. No new dependencies. See `docs/adr/0033-search-volume-controls.md`. (closes #213)

- **Project Gutenberg adapter** — extracts books as chapter-structured markdown. Three-tier fallback chain: EPUB → plain text → generic pipeline. Zero new dependencies. Enriches metadata via Gutendex API (title, author, subjects, language). Registered at priority 200. See `scraper-svc/scraper/adapters/gutenberg.py`. (closes #181)

- **Batch vector ingestion via Qdrant gRPC (ADR-0030)** — adds `POST /index/batch` to semantic-svc for batched embedding and Qdrant upsert. Batch scrape and crawl workers now accumulate pages and fire a single batch call instead of N per-page calls. Expected: 500-page crawl indexing drops from ~50s to ~250ms (200x). New tests: `test_batch_index_endpoint`, `test_batch_index_empty`. Legacy flat-vector Qdrant collections auto-migrate to named vectors on startup. See `docs/adr/0030-batch-vector-ingestion.md`. (closes #154)

- **Service-level metrics for semantic-svc (ADR-0029)** — adds Prometheus-compatible `/metrics` endpoint to semantic-svc with stdlib-based OpenMetrics format (no new dependencies). Metrics tracked: document count gauge (`groktocrawl_index_docs_total`), eviction counter (`groktocrawl_index_evictions_total`), request latency histogram per endpoint (`groktocrawl_index_query_duration_seconds`), embedding inference duration (`groktocrawl_index_embeddings_duration_seconds`), and request counter per endpoint (`groktocrawl_search_requests_total`). ASGI middleware instruments all 11 existing endpoints automatically. Eviction counter tracks cumulative evictions via `_evict_if_needed()`. See `docs/adr/0029-service-level-metrics-for-semantic-svc.md`. (closes #153)

- **Embedding model migration path — named vectors, backfill, dual-write, cutover (ADR-0028)** — supports upgrading the embedding model without dropping the index. Uses Qdrant named vectors for per-point multi-model storage. New endpoints: `GET /index/model` (model config + migration state), `POST /index/migrate/start` (begin backfill), `GET /index/migrate/status` (progress), `POST /index/migrate/cutover` (switch queries). New payload fields: `embedding_model`, `embedding_dim`, `embedding_models` (history). Dual-write mode indexes with both old and new model during migration. Old vectors retained after cutover for rollback safety. See `docs/adr/0028-embedding-model-migration-path.md`. (closes #155)

- **Unit test coverage for worker.py and research.py** — adds `test_worker.py` (19 tests, 693 lines) covering all 7 worker processing functions, and 23 new tests for previously uncovered research.py functions (`run_extract`, `run_research_stream`, `run_answer_stream`, `run_rich_search`, `_rerank_answer_sources`). Agent-svc/agent coverage from ~15% to 55%. Coverage threshold raised to 20%. (closes #192)

### Fixed

- **Playwright health probe and Tier-3 integration tests** — adds `test_scraper_health_reports_playwright` and `test_scraper_falls_through_to_playwright` to catch missing `playwright install-deps chromium` (fixes #258). Adds `tier3-fixture` service with JS-rendered dynamic content to force Playwright fallback. Scraper `/health` now returns `checks.playwright.available`. (closes #260)

- **Default URL scheme typos in settings** — all four agent-svc default URLs (`scraper_url`, `searxng_url`, `semantic_url`, `llm_base_url`) had `http//` instead of `http://`, causing agent to fail when `.env` didn't explicitly set these. (closes #260)

- **CI workflow fixture setup** — `llm-svc` was not started in the "Start test fixtures" step, breaking the 2 streaming endpoint tests. `LLM_BASE_URL` and `LLM_MODEL` now written to `.env` before stack startup so the agent daemon uses the fixture LLM. Semantic-svc health check now validates Qdrant reachability from inside the agent-svc container. (closes #260)

- **Flaky activity feed tests** — `test_activity_shows_active_crawl_job` and `test_activity_multi_type` failed when fast single-page crawls completed before the activity feed check. Both now fall back to the job status endpoint when the job is no longer in the active feed. (closes #260)

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
