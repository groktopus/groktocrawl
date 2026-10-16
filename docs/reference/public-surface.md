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
POST /v2/followup/preview
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
POST /v2/parse/upload-url
PUT /v2/parse/upload/{upload_id}
POST /v2/research-memory/query
POST /v2/research-memory/store
DELETE /v2/research-memory/{artifact_id}
POST /v2/scrape
POST /v2/search
POST /v2/session/create
GET /v2/session/{session_id}
GET /v2/session/{session_id}/evidence/{ref_id}
POST /v2/session/{session_id}/export
POST /v2/session/{session_id}/resolve
POST /v2/session/{session_id}/step
POST /v2/session/{session_id}/documents
GET /v2/session/{session_id}/documents
GET /v2/session/{session_id}/documents/{ref_id}
DELETE /v2/session/{session_id}/documents/{ref_id}
DELETE /v2/session/{session_id}
GET /experimental/research/v1/capabilities
POST /experimental/research/v1/runs
GET /experimental/research/v1/runs/{run_id}
GET /experimental/research/v1/runs/{run_id}/events
POST /experimental/research/v1/runs/{run_id}/cancel
GET /experimental/research/v1/artifact-sets/{artifact_set_id}
GET /experimental/research/v1/artifacts/{artifact_id}
GET /experimental/research/v1/research/{research_id}/evidence/{snapshot_id}
DELETE /experimental/research/v1/research/{research_id}
POST /experimental/research/v1/sessions/{session_id}/attachments
GET /experimental/research/v1/workspace
GET /experimental/research/v1/workspace/{run_id}
POST /experimental/research/v1/workspace/{run_id}/actions
<!-- api-inventory:end -->

## CLI commands

<!-- cli-inventory:start -->
- active
- agent
- followup
- answer
- batch-scrape
- browser
- crawl
- document
- download
- enrich
- evidence
- extract
- find-similar
- generate-llmstxt
- map
- monitor
- parse
- parse-upload
- research
- scrape
- search
<!-- cli-inventory:end -->

## Compose services

<!-- service-inventory:start -->
- agent-svc
- agent-svc-fixture
- browser-svc
- flare-solverr
- llm-svc
- mcp-svc
- ofelia
- parse-svc
- portal-svc
- qdrant
- research-postgres
- scraper-svc
- semantic-svc
- slopsearx
- slopsearx-fixture
- slopsearx-mcp
- test-site
- tier3-fixture
- valkey
<!-- service-inventory:end -->

## Configuration keys

The configuration inventory follows `.env.sample`; unlisted implementation-only defaults and test variables are intentionally excluded.
The protected Compose override's `PROTECTED_*` settings configure its isolated
network subnets, address pools, gateways, and static service addresses.
`LLM_GATEWAY_PRIVATE_HOSTS` is a model-gateway-only allowlist for an explicitly
configured private model endpoint; it does not permit private scrape targets.

<!-- env-inventory:start -->
- ACTIVE_EMBED_MODEL
- ADAPTER_ABUSEIPDB_API_KEY
- ADAPTER_ASHBY_API_KEY
- ADAPTER_CENSYS_API_ID
- ADAPTER_CENSYS_API_SECRET
- ADAPTER_GUTENBERG_CACHE_BASE
- ADAPTER_GUTENDEX_API_BASE
- ADAPTER_HIBP_API_KEY
- ADAPTER_NVD_API_KEY
- ADAPTER_OTX_API_KEY
- ADAPTER_SHODAN_API_KEY
- ADAPTER_VIRUSTOTAL_API_KEY
- ADAPTER_VULNCHECK_API_KEY
- ADAPTER_YOUTUBE_API_KEY
- ADMISSION_BROWSER_LIMIT
- ADMISSION_LIGHT_FETCH_LIMIT
- ADMISSION_LLM_LIMIT
- AGENT_MAX_SEARCHES_PER_REQUEST
- AGENT_SEARCH_RATE_LIMIT
- API_KEY
- ASHBY_API_KEY
- BRAVE_API_KEY
- BROWSER_SVC_URL
- CAPTCHA_VISION_API_KEY
- CAPTCHA_VISION_BASE_URL
- CAPTCHA_VISION_MODEL
- CAPTCHA_VISION_TIMEOUT
- CIRCUIT_BREAKER_COOLDOWN_SECONDS
- CIRCUIT_BREAKER_FAILURE_THRESHOLD
- CRAWL_IDLE_TIMEOUT_SECONDS
- CRAWL_MAX_DURATION_SECONDS
- DURABLE_RESEARCH_POSTGRES_DSN
- DURABLE_RESEARCH_POSTGRES_PASSWORD_FILE
- DURABLE_RESEARCH_REDIS_URL
- EMBED_DIM
- EMBED_MODEL_NAME
- ENGINE_SEMANTICSCHOLAR_API_KEY
- FEATURE_EXPERIMENTAL_RESEARCH
- FEATURE_EXPERIMENTAL_RESEARCH_DURABLE
- FEATURE_EXPERIMENTAL_RESEARCH_POSTGRES_ARTIFACTS
- FEATURE_EXPERIMENTAL_RESEARCH_RUNS
- FLARE_SOLVERR_URL
- GITHUB_TOKEN
- GROKTOCRAWL_API_KEY
- GROKTOCRAWL_RETRY_FALLBACK_SECONDS
- GROKTOCRAWL_RETRY_MAX_ATTEMPTS
- GROKTOCRAWL_RETRY_MAX_WAIT_SECONDS
- GROKTOCRAWL_REVISION
- HTTP_TIMEOUT
- JOB_RETRY_FALLBACK_SECONDS
- JOB_RETRY_MAX_ATTEMPTS
- JOB_RETRY_MAX_WAIT_SECONDS
- LLM_API_KEY
- LLM_BASE_URL
- LLM_CALL_TIMEOUT
- LLM_ENABLE_THINKING
- LLM_HEALTH_TOKEN_BUDGET
- LLM_GATEWAY_PRIVATE_HOSTS
- LLM_LLAMA_CPP_DISABLE_THINKING
- LLM_MAX_COMPLETION_TOKENS
- LLM_MAX_OUTPUT_TOKENS
- LLM_MAX_TOKENS
- LLM_MODEL
- LLM_TOKEN_PARAMETER
- LOG_LEVEL
- MCP_ALLOWED_HOSTS
- MCP_ALLOWED_ORIGINS
- MCP_GRANT_DEPENDENCY_DOSSIER
- MCP_GRANT_JOBS
- MCP_GRANT_RESEARCH
- MCP_GRANT_RETRIEVAL_RECEIPTS
- MCP_GRANT_SAVED_SEARCH_EVENTS
- MCP_GRANT_SAVED_SEARCHES
- MCP_GRANT_SCIENCE
- MCP_GRANT_SECURITY
- MCP_GRANT_STAGED_SEARCH
- MCP_PORT
- MCP_TARGETED_SENSITIVE_ALLOWED
- NEAR_DUP_MODE
- NEAR_DUP_THRESHOLD
- PARSE_MAX_SIZE_MB
- PGVECTOR_SHADOW_BACKFILL_BATCH_SIZE
- PGVECTOR_SHADOW_DSN
- PGVECTOR_SHADOW_SAMPLE_RATE
- PGVECTOR_SHADOW_SCORE_TOLERANCE
- PGVECTOR_SHADOW_TIMEOUT_SECONDS
- PROTECTED_CAPTURE_BROWSER_GATEWAY_IP
- PROTECTED_CAPTURE_BROWSER_POOL
- PROTECTED_CAPTURE_BROWSER_RENDERER_IP
- PROTECTED_CAPTURE_BROWSER_SUBNET
- PROTECTED_CAPTURE_EGRESS_SUBNET
- PROTECTED_CAPTURE_FLARE_GATEWAY_IP
- PROTECTED_CAPTURE_FLARE_POOL
- PROTECTED_CAPTURE_FLARE_RENDERER_IP
- PROTECTED_CAPTURE_FLARE_SUBNET
- PROTECTED_CAPTURE_SOURCE_GATEWAY
- PROTECTED_CAPTURE_SOURCE_IP
- PROTECTED_CAPTURE_SOURCE_POOL
- PROTECTED_CAPTURE_SOURCE_SUBNET
- PROTECTED_CONTROL_PRIVATE_SUBNET
- PROTECTED_MODEL_GATEWAY_IP
- PROTECTED_MODEL_GATEWAY_POOL
- PROTECTED_MODEL_GATEWAY_SUBNET
- PROTECTED_MODEL_UPSTREAM_SUBNET
- PROTECTED_SCRAPER_PRIVATE_SUBNET
- QA_MAX_BOILERPLATE_RATIO
- QA_MIN_CONTENT_CHARS
- QA_MIN_QUALITY_THRESHOLD
- QA_MIN_TITLE_CHARS
- QDRANT_CLIENT_TIMEOUT
- QDRANT_QUERY_TIMEOUT
- QDRANT_URL
- RECOVERY_LLM_TIMEOUT
- RESEARCH_MEMORY_MAX_ARTIFACT_BYTES
- RESEARCH_MEMORY_PGVECTOR_DSN
- RESEARCH_MEMORY_SCOPE
- RESEARCH_MEMORY_TTL
- RESEARCH_MEMORY_VECTOR_MODE
- SCRAPE_CACHE_DOMAIN_TTLS
- SCRAPE_CACHE_MAX_TTL
- SCRAPE_CACHE_MIN_TTL
- SCRAPE_CACHE_STABLE_MULTIPLIER
- SCRAPE_CACHE_TTL
- SCRAPE_CACHE_VOLATILE_CAP
- SCRAPER_BROWSER_POOL_ENABLED
- SCRAPER_BROWSER_POOL_IDLE_TTL_SECONDS
- SCRAPER_BROWSER_POOL_MAX_AGE_SECONDS
- SCRAPER_BROWSER_POOL_MAX_PROCESSES
- SCRAPER_DISTRIBUTED_POLITENESS
- SCRAPER_MAX_BROWSER_CONCURRENCY
- SCRAPER_POLITENESS_CRAWL_DELAY
- SCRAPER_POLITENESS_ENABLED
- SCRAPER_POLITENESS_ROBOTS_TIMEOUT
- SCRAPER_POLITENESS_ROBOTS_TTL
- SCRAPER_PRIVATE_URL_ALLOWLIST
- SCRAPER_PROXY_URL
- SCRAPER_URL
- SEARXNG_URL
- SECTION_FILTER_DEFAULT_EXCLUDE
- SECTION_FILTER_DEFAULT_INCLUDE
- SECTION_FILTER_DEFAULT_VERBOSITY
- SEMANTIC_CLIENT_TIMEOUT_SECONDS
- SEMANTIC_INFERENCE_ADMISSION_TIMEOUT_SECONDS
- SEMANTIC_INFERENCE_QUEUE_SIZE
- SEMANTIC_INFERENCE_WORKERS
- SEMANTIC_URL
- SESSION_SWEEP_INTERVAL
- SESSION_TTL
- SLOPSEARX_MCP_AUTH_TOKEN
- SLOPSEARX_MCP_PORT
- SLOPSEARX_PROVENANCE_MCP_URL
- SLOPSEARX_PROVENANCE_PROFILE
- SLOPSEARX_PROVENANCE_SNAPSHOT_TTL_SECONDS
- SLOPSEARX_PROVENANCE_TOKEN
- TYPESAFE_API_KEY
- TYPESAFE_JEV_MIN_SCORE
- VALKEY_URL
- VECTOR_INDEX_MAX_DOCS
- VECTOR_STORE_MODE
- WEBHOOK_SECRET
<!-- env-inventory:end -->

## Bounded evidence controls

`agent` and `answer` accept optional `--evidence-budget-chars` (256–128000),
matching API/MCP `evidence_budget_chars`. Omission preserves server defaults.
CLI JSON retains `evidence_coverage` in synchronous and streaming completion
results. This budget selects verbatim source characters for synthesis; it does
not limit acquisition or claim exhaustive evidence coverage.

`groktocrawl evidence SESSION_ID REF_ID --query "question" --json` and MCP
`select_session_evidence` inspect exact retained spans, content digest and
coverage without retrieval or model work. Omit query to page from `--offset`
(default 0), using `--budget-chars` (default 32000, range 256–128000). Query
selection and a nonzero offset are mutually exclusive. Follow the returned
continuation with its offset and `--expected-digest` (64 lowercase hexadecimal
characters); changed content is rejected. `coverage_complete` remains false.
Session ownership, reference liveness, deletion and existing TTL govern access.
