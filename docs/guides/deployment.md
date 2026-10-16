# Deployment and configuration

## Services and profiles

`docker compose up -d` starts the production service graph. `docker compose --profile fixture up --build -d` additionally starts `llm-svc`, `test-site`, `tier3-fixture`, `slopsearx-fixture`, and `agent-svc-fixture` for local evaluation. Semantic indexing is optional and best-effort on constrained hosts; before first enabling it, create the external model-cache volume with `docker volume create hf-cache`, then start `semantic-svc` and Qdrant with `docker compose --profile indexing up -d`. Without that profile, ordinary scrape and keyword/deep search continue, while vector and hybrid-vector retrieval, semantic/hybrid reranking, `/v2/find-similar`, and semantic-backed research-memory indexing are unavailable. The main public ports are agent API `8080`, fixture agent API `8084` when the fixture profile is enabled, portal `8082`, scraper `8001`, semantic service `8003` when indexing is enabled, SlopSearX `8081`, GroktoCrawl MCP `8002`, and direct SlopSearX MCP `8007` when the `mcp` profile is enabled.

`agent-svc` coordinates requests; `scraper-svc` fetches content; optional `semantic-svc` uses Qdrant by default; Valkey stores operational state; SlopSearX discovers web results; `browser-svc`, `parse-svc`, `portal-svc`, `mcp-svc`, `slopsearx-mcp`, and Ofelia provide specialized capabilities. `mcp-svc` exposes GroktoCrawl API tools; the opt-in `slopsearx-mcp` companion exposes direct SlopSearX search-engine tools when the `mcp` profile is enabled. The [architecture guide](../architecture.md) describes ownership and data flow.

Research memory stores full artifacts in Valkey and defaults to Qdrant vectors.
After starting the `durable-research` profile and following the
[research-memory pgvector runbook](../runbooks/research-memory-pgvector.md),
operators may select PostgreSQL shadow or PostgreSQL-only vector mode. This is
separate from semantic page-index `VECTOR_STORE_MODE`; `pgvector_only` removes
Qdrant from that service's readiness and request path, while the existing
`pgvector` mode retains Qdrant writes for immediate rollback. For a
PostgreSQL-only semantic service, set `VECTOR_STORE_MODE=pgvector_only` and use
the dedicated `pgvector-only` Compose profile without `indexing` or
`durable-research`; Qdrant is not started by that profile. Existing Qdrant
containers and data remain available for rollback.

The published GroktoCrawl service images support both `linux/amd64` and
`linux/arm64`, so arm64 hosts such as Apple Silicon Macs do not need to run
these images under emulation. The optional Qdrant service remains explicitly
configured for `linux/amd64` in Compose.

## Configuration

For deterministic Compose integration runs, enable the fixture profile and send
critical-journey requests to `http://localhost:8084` from the host or
`http://agent-svc-fixture:8080` from another Compose service. Do not override
the ordinary `agent-svc` search URL. The source-owned `slopsearx-fixture` is a
versioned contract emulator with deterministic scenarios and process-local
diagnostic state; it is not a ranking or index replica and is not a production
default. CI runs a separate `agent-svc-fixture` instance against that boundary,
leaving the ordinary integration service on the configured production-compatible
search path.

The LLM fixture selects a scenario through
`http://llm-svc:8011/v1/scenarios/<scenario>`; `LLMClient` appends
`/chat/completions` without rewriting the base URL. The legacy
`/v1/chat/completions` route remains the default behavior. Scenario semantics
use `SCHEMA_VERSION` for the HTTP contract and `FIXTURE_VERSION` for scenario
behavior. Tests should provide a path-safe `run_id` and filter
`/diagnostics?run_id=...`; diagnostics are bounded and contain no prompts,
context, authorization headers, or secrets. This is a contract emulator, not a
provider-quality model; its validity ceiling is the documented contract.

Copy `.env.sample` to `.env` and configure an OpenAI-compatible LLM for non-fixture use. `BRAVE_API_KEY` is required for useful open-web search results. The [configuration inventory](../reference/public-surface.md#configuration-keys) is validated against `.env.sample`; it separates provider, service URLs, vector index, adapters, cache, politeness, search controls, crawl limits, and research-memory settings.

Optional `TYPESAFE_API_KEY` is an explicit operator opt-in to paid TypeSafe Jev
specialist routing in SlopSearX. The stack passes a nonempty key from `.env`
to both the HTTP/portal service and the direct `slopsearx-mcp` companion;
leaving it unset keeps both on deterministic routing. The unrelated
GroktoCrawl `mcp-svc` does not need this key.

Only expose or override internal service URLs when deliberately splitting the compose deployment. Persist Valkey and Qdrant volumes in production; the embedding model cache volume avoids repeated model downloads.

### Direct SlopSearX MCP grants

`slopsearx-mcp` is an opt-in companion started with `docker compose --profile mcp up`. It requires a non-empty
`SLOPSEARX_MCP_AUTH_TOKEN`; the container refuses to start without it (a no-config `docker compose up` does not abort). Its host
port is `SLOPSEARX_MCP_PORT` (default `8007`). The companion shares the normal
Brave credential and Valkey service wiring, but no grant creates credentials or
bypasses HTTP MCP authentication.

All grants default to disabled (secure-by-default, inherited from the
SlopSearX image): `MCP_GRANT_JOBS` enables jobs tools,
`MCP_GRANT_SCIENCE` science tools, `MCP_GRANT_RESEARCH` research tools,
`MCP_GRANT_STAGED_SEARCH` staged dispatch,
`MCP_GRANT_RETRIEVAL_RECEIPTS` retrieval receipts and manifests,
`MCP_GRANT_SAVED_SEARCHES` saved searches and change reports,
`MCP_GRANT_SAVED_SEARCH_EVENTS` their event outbox,
`MCP_GRANT_DEPENDENCY_DOSSIER` dependency investigations,
`MCP_GRANT_SECURITY` security tools, and
`MCP_TARGETED_SENSITIVE_ALLOWED` targeted sensitive-engine selection
(`hibp`, `dehashed`). Opt in per capability group in `.env`, for example
`MCP_GRANT_STAGED_SEARCH=1`; leave every unused capability unset and leave
`MCP_TARGETED_SENSITIVE_ALLOWED` unset unless sensitive-engine queries are
explicitly wanted.

The service healthcheck sends a bounded, authenticated MCP `initialize`
request to its local Streamable HTTP endpoint. It verifies MCP protocol
readiness without invoking search, so it does not need a provider credential
beyond the service token. This repository tests the rendered configuration; a
live protocol probe requires a Docker-capable deployment environment.

CAPTCHA image-grid recovery is optional. Set all of `CAPTCHA_VISION_BASE_URL`,
`CAPTCHA_VISION_API_KEY`, and `CAPTCHA_VISION_MODEL` to an OpenAI-compatible
multimodal endpoint; incomplete configuration skips vision. Use
`CAPTCHA_VISION_TIMEOUT` to bound image requests (default: 60 seconds). The
scraper mounts a
runtime-only CloakBrowser cache at `/root/.cloakbrowser`, attempts its official
binary download at startup, and falls back to stock Playwright Chromium if that
download fails. CloakBrowser uses its own upstream browser context defaults;
the legacy Chromium fingerprint shim applies only to the stock fallback. The
wrapper is MIT, while the downloaded binary is not copied into image layers or
redistributed by GroktoCrawl.

`SCRAPER_MAX_BROWSER_CONCURRENCY` bounds complete Playwright browser lifecycles
inside `scraper-svc` (default: 4, allowed range: 1–32). This service-wide limit
applies across independent callers and jobs. The Compose service also runs with
an init process so orphaned browser descendants are reaped.

## Security

- Set a strong `API_KEY` and route public access through TLS/reverse-proxy controls.
- Keep internal service ports private where possible; the API emits a warning header if authentication is disabled.
- Use `WEBHOOK_SECRET` to authenticate outbound asynchronous notifications.
- `SCRAPER_PROXY_URL` retains the existing operator-managed per-job proxy behavior.
  For source HTTPX and curl traffic, `SCRAPER_CAPTURE_EGRESS_PROXY_URL` opts into
  the guarded forward proxy: it must be an absolute `http://host[:port]` URL,
  overrides the per-job proxy for source requests, and disables environment
  proxy discovery. Invalid configuration and proxy failure do not retry source
  traffic directly. The experimental candidate Compose profile sets this route
  and delegates protected browser rendering to its isolated renderer. In that
  candidate profile, browser/Flare/model/Valkey control operations and the
  agent-facing scraper API use fixed Unix-socket bridges, while source requests
  use the capture gateway. The composition remains unqualified until its
  combined Docker Runtime Gate and negative-boundary probes pass; see the
  experimental composition status before treating it as a deployment security
  boundary.
- Private and internal destinations are blocked before every fetch tier. Keep `SCRAPER_PRIVATE_URL_ALLOWLIST` empty unless an exact, trusted internal hostname must be reachable; CI uses it only for fixture services.
- Enable `SCRAPER_POLITENESS_ENABLED` for per-domain rate limiting and robots.txt enforcement when required by your deployment policy.

## Operations

`/health` reports dependency probes and `/metrics` exposes OpenMetrics data. Prometheus alerts and response procedures live in [runbooks](../runbooks/README.md). Important capacity controls include `AGENT_MAX_SEARCHES_PER_REQUEST`, `AGENT_SEARCH_RATE_LIMIT`, crawl duration/idle limits, scrape-cache TTLs, and vector-index capacity.

### Semantic search readiness

The optional `semantic-svc` `/health` probe checks that models have loaded and
that Qdrant answers `get_collections()`. It does **not** embed a query, search a
collection, validate indexed content, or measure vector-search latency. The
agent service's aggregate `/health` does not probe semantic retrieval either.
A green health response is therefore not evidence that a representative vector
query will succeed within your latency target.

Validate that separately against a populated index using `POST /v2/search` with
`{"query":"a representative query for your indexed content","retrieval_mode":"vector","limit":5}`.
Use your deployment's normal authentication, check the returned results, and
record latency across repeated requests under representative load. Non-streaming
vector requests return a sanitized HTTP 503 if the semantic service returns an
HTTP error or cannot be reached. Streaming requests have already sent their HTTP
headers, so they emit a sanitized `error` event and end the stream instead of
emitting a successful `done` event. Hybrid-vector retrieval retains its web-only
fallback when the vector service is unavailable.

### Job durability and recovery

Async jobs (crawl, agent, extract, batch-scrape, llmstxt, and plan execution) run in-process inside `agent-svc`, with Valkey-backed ownership and recovery (ADR-0072). On restart, records still queued and never claimed can be reconstructed from their persisted normalized request and dispatched. Work that had started is not replayed: after the 45-second lease expires it becomes `failed` with `OUTCOME_UNKNOWN`, because an external side effect may already have happened. Records created before the versioned execution marker are failed the same way. Completion/failure webhook intents are committed with the terminal transition and replayed at-least-once with the original `webhookId`; webhook receivers must deduplicate that ID. Existing partial artifacts are not rolled back, and this does not provide exactly-once execution or a completion SLO.

After an `agent-svc` restart, allow queued jobs to be redispatched and running leases to expire. Check for jobs still `processing` after the lease window and inspect `OUTCOME_UNKNOWN` failures before deciding whether to resubmit:

```bash
# List all processing jobs (API)
curl -s http://localhost:8080/v2/activity

# Flag processing jobs older than the lease window (dry-run, nothing changes)
docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py --stale-after 60

# Manual fallback only for records still stranded after startup recovery.
# Stop agent-svc first and inspect effects before resubmitting.
docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py --stale-after 60 --fail --assume-stopped
```

The reconcile tool reads `VALKEY_URL` inside the container; from a checkout, pass `--redis-url`. It refuses `--fail` without `--assume-stopped`. Use it only if startup recovery has not resolved a stale record; it is not a substitute for checking an `OUTCOME_UNKNOWN` job's external effects. Full symptoms, identification, and resolution steps: [Interrupted Jobs runbook](../runbooks/interrupted-jobs.md). The recovery boundary is recorded in [ADR-0072](../adr/0072-valkey-leased-legacy-job-recovery.md), which updates the prior deferred direction in [ADR-0047](../adr/0047-defer-restart-safe-execution.md).

CAPTCHA screenshots are challenge-widget crops held only in memory for the
configured vision request. They are never logged, cached, returned, or written
to disk. Exhausted recovery returns `CAPTCHA_UNRESOLVED`; it does not guarantee
access to protected pages.

The fixture-backed critical-journey release gate checks `/health`, the fast-search response contract, and `/v2/scrape` for `test-site`'s markdown-capable `/pricing` page; it deliberately does not promise live-search result cardinality, semantic retrieval, or research-agent results.

Before upgrading, run `docker compose config --quiet`, rebuild changed services, and review [CHANGELOG.md](../../CHANGELOG.md). Use the fixture profile and test suite before changing production configuration.
