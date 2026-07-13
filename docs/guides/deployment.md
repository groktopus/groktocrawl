# Deployment and configuration

## Services and profiles

`docker compose up -d` starts the production service graph. `docker compose --profile fixture up --build -d` additionally starts `llm-svc`, `test-site`, and `tier3-fixture` for local evaluation. The main public ports are agent API `8080`, portal `8082`, scraper `8001`, semantic service `8003`, SlopSearX `8081`, and MCP `8002` by default.

`agent-svc` coordinates requests; `scraper-svc` fetches content; `semantic-svc` uses Qdrant; Valkey stores operational state; SlopSearX discovers web results; `browser-svc`, `parse-svc`, `portal-svc`, `mcp-svc`, and Ofelia provide specialized capabilities. The [architecture guide](../architecture.md) describes ownership and data flow.

## Configuration

Copy `.env.sample` to `.env` and configure an OpenAI-compatible LLM for non-fixture use. `BRAVE_API_KEY` is required for useful open-web search results. The [configuration inventory](../reference/public-surface.md#configuration-keys) is validated against `.env.sample`; it separates provider, service URLs, vector index, adapters, cache, politeness, search controls, crawl limits, and research-memory settings.

Only expose or override internal service URLs when deliberately splitting the compose deployment. Persist Valkey and Qdrant volumes in production; the embedding model cache volume avoids repeated model downloads.

## Security

- Set a strong `API_KEY` and route public access through TLS/reverse-proxy controls.
- Keep internal service ports private where possible; the API emits a warning header if authentication is disabled.
- Use `WEBHOOK_SECRET` to authenticate outbound asynchronous notifications.
- Configure `SCRAPER_PROXY_URL` only for an operator-managed outbound proxy; credentials are redacted in logs and requests fail open if that proxy is unavailable.
- Enable `SCRAPER_POLITENESS_ENABLED` for per-domain rate limiting and robots.txt enforcement when required by your deployment policy.

## Operations

`/health` reports dependency probes and `/metrics` exposes OpenMetrics data. Prometheus alerts and response procedures live in [runbooks](../runbooks/README.md). Important capacity controls include `AGENT_MAX_SEARCHES_PER_REQUEST`, `AGENT_SEARCH_RATE_LIMIT`, crawl duration/idle limits, scrape-cache TTLs, and vector-index capacity.

The fixture-backed critical-journey diagnostic checks `/health`, the fast-search response contract, and `/v2/scrape` for `test-site`'s substantive `/content/multi-sentence` page; it deliberately does not promise live-search result cardinality, semantic retrieval, or research-agent results.

Before upgrading, run `docker compose config --quiet`, rebuild changed services, and review [CHANGELOG.md](../../CHANGELOG.md). Use the fixture profile and test suite before changing production configuration.
