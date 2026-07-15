# Public surface inventory

This inventory is validated by `scripts/check-docs-surface.py`. It is intentionally compact: OpenAPI remains the source of truth for schemas, while this file makes public-surface drift visible in review.

## API routes

<!-- api-inventory:start -->
GET /v2/activity
POST /v2/agent
POST /v2/agent/execute
POST /v2/agent/plan
GET /v2/agent/plan/{plan_id}
GET /v2/agent/{job_id}
DELETE /v2/agent/{job_id}
POST /v2/answer
POST /v2/batch/scrape
GET /v2/batch/scrape/{job_id}
DELETE /v2/batch/scrape/{job_id}
GET /v2/batch/scrape/{job_id}/errors
POST /v2/browser
GET /v2/browser
POST /v2/browser/{session_id}/execute
DELETE /v2/browser/{session_id}
POST /v2/citations/resolve
POST /v2/crawl
GET /v2/crawl/active
POST /v2/crawl/params-preview
GET /v2/crawl/{job_id}
DELETE /v2/crawl/{job_id}
GET /v2/crawl/{job_id}/errors
GET /v2/crawl/{job_id}/stream
POST /v2/enrich
POST /v2/extract
GET /v2/extract/{job_id}
POST /v2/find-similar
POST /v2/generate-llmstxt
GET /v2/generate-llmstxt/{job_id}
POST /v2/map
POST /v2/memory/batch/query
POST /v2/memory/batch/store
POST /v2/memory/sweep
GET /v2/memory/{memory_id}
DELETE /v2/memory/{memory_id}
POST /v2/monitor
GET /v2/monitor
GET /v2/monitor/{monitor_id}
PATCH /v2/monitor/{monitor_id}
DELETE /v2/monitor/{monitor_id}
POST /v2/monitor/{monitor_id}/run
POST /v2/parse
PUT /v2/parse/upload/{upload_id}
POST /v2/research-memory/query
POST /v2/research-memory/store
DELETE /v2/research-memory/{artifact_id}
POST /v2/scrape
POST /v2/search
POST /v2/session/create
GET /v2/session/{session_id}
POST /v2/session/{session_id}/export
POST /v2/session/{session_id}/resolve
POST /v2/session/{session_id}/step
DELETE /v2/session/{session_id}
<!-- api-inventory:end -->

## CLI commands

<!-- cli-inventory:start -->
- active
- agent
- answer
- batch-scrape
- browser
- crawl
- download
- enrich
- extract
- find-similar
- generate-llmstxt
- map
- monitor
- parse
- parse-upload
- scrape
- search
<!-- cli-inventory:end -->

## Compose services

<!-- service-inventory:start -->
- agent-svc
- browser-svc
- flare-solverr
- llm-svc
- mcp-svc
- ofelia
- parse-svc
- portal-svc
- qdrant
- scraper-svc
- semantic-svc
- slopsearx
- test-site
- tier3-fixture
- valkey
<!-- service-inventory:end -->

## Configuration keys

The configuration inventory follows `.env.sample`; unlisted implementation-only defaults and test variables are intentionally excluded.

<!-- env-inventory:start -->
- ACTIVE_EMBED_MODEL
- ADAPTER_ABUSEIPDB_API_KEY
- ADAPTER_CENSYS_API_ID
- ADAPTER_CENSYS_API_SECRET
- ADAPTER_HIBP_API_KEY
- ADAPTER_NVD_API_KEY
- ADAPTER_OTX_API_KEY
- ADAPTER_SHODAN_API_KEY
- ADAPTER_VIRUSTOTAL_API_KEY
- ADAPTER_VULNCHECK_API_KEY
- ADAPTER_YOUTUBE_API_KEY
- AGENT_MAX_SEARCHES_PER_REQUEST
- AGENT_SEARCH_RATE_LIMIT
- API_KEY
- BROWSER_SVC_URL
- BRAVE_API_KEY
- CAPTCHA_VISION_API_KEY
- CAPTCHA_VISION_BASE_URL
- CAPTCHA_VISION_MODEL
- CAPTCHA_VISION_TIMEOUT
- CIRCUIT_BREAKER_COOLDOWN_SECONDS
- CIRCUIT_BREAKER_FAILURE_THRESHOLD
- CRAWL_IDLE_TIMEOUT_SECONDS
- CRAWL_MAX_DURATION_SECONDS
- EMBED_DIM
- EMBED_MODEL_NAME
- FLARE_SOLVERR_URL
- GITHUB_TOKEN
- GROKTOCRAWL_API_KEY
- HTTP_TIMEOUT
- LLM_API_KEY
- LLM_BASE_URL
- LLM_ENABLE_THINKING
- LLM_LLAMA_CPP_DISABLE_THINKING
- LLM_MODEL
- LOG_LEVEL
- MCP_PORT
- NEAR_DUP_MODE
- NEAR_DUP_THRESHOLD
- QDRANT_URL
- PARSE_MAX_SIZE_MB
- QA_MAX_BOILERPLATE_RATIO
- QA_MIN_CONTENT_CHARS
- QA_MIN_QUALITY_THRESHOLD
- QA_MIN_TITLE_CHARS
- RECOVERY_LLM_TIMEOUT
- RESEARCH_MEMORY_MAX_ARTIFACT_BYTES
- RESEARCH_MEMORY_SCOPE
- RESEARCH_MEMORY_TTL
- SCRAPER_POLITENESS_CRAWL_DELAY
- SCRAPER_POLITENESS_ENABLED
- SCRAPER_POLITENESS_ROBOTS_TTL
- SCRAPER_POLITENESS_ROBOTS_TIMEOUT
- SCRAPER_PRIVATE_URL_ALLOWLIST
- SCRAPER_PROXY_URL
- SCRAPER_URL
- SCRAPE_CACHE_DOMAIN_TTLS
- SCRAPE_CACHE_MAX_TTL
- SCRAPE_CACHE_MIN_TTL
- SCRAPE_CACHE_STABLE_MULTIPLIER
- SCRAPE_CACHE_TTL
- SCRAPE_CACHE_VOLATILE_CAP
- SEARXNG_URL
- SEMANTIC_URL
- SECTION_FILTER_DEFAULT_EXCLUDE
- SECTION_FILTER_DEFAULT_INCLUDE
- SECTION_FILTER_DEFAULT_VERBOSITY
- SESSION_SWEEP_INTERVAL
- SESSION_TTL
- VALKEY_URL
- VECTOR_INDEX_MAX_DOCS
- WEBHOOK_SECRET
<!-- env-inventory:end -->
