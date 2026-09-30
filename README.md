# GroktoCrawl

GroktoCrawl is a self-hosted, MIT-licensed web data platform compatible with the Firecrawl v2 API surface. It combines scraping, crawl and map jobs, search, structured extraction, browser automation, monitors, semantic retrieval, an autonomous research agent, and an MCP server in one Docker deployment.

It is designed as a local-first, observable, model-flexible foundation for organizations that want agentic research and web intelligence without surrendering control of their data, infrastructure, or model choices.

## Start here

Choose your path. Both run the same Docker stack defined in `docker-compose.yml`; they differ in which services and credentials you configure.

### Local demo (fixture profile)

The fastest end-to-end smoke test, with no external credentials. The `fixture` profile starts a local LLM fixture (`llm-svc`) and two fixture test sites (`test-site`, `tier3-fixture`).

**Config:** copy `.env.sample` to `.env`. No credentials are required; the optional direct SlopSearX MCP companion (`slopsearx-mcp`) is opt-in via the `mcp` Compose profile and only then needs a non-empty `SLOPSEARX_MCP_AUTH_TOKEN`.

```bash
cp .env.sample .env
docker compose --profile fixture up --build -d
curl http://localhost:8080/health
./groktocrawl scrape https://example.com
```

**Expected success output:** `curl http://localhost:8080/health` returns `{"status":"ok", "checks":{...}}` once all dependencies are up, and `./groktocrawl scrape https://example.com` prints example.com rendered as markdown.

### Production minimum (no fixture profile)

The same stack pointed at a real LLM provider, an open-web search backend, and a hardened API — without the fixture-only services.

**Config (in `.env`):** an OpenAI-compatible LLM provider (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), `BRAVE_API_KEY` for web search, and `API_KEY` for API authentication. `llm-svc`, `test-site`, and `tier3-fixture` are fixture-only and must **not** be started in production; omit the `fixture` profile. The optional direct SlopSearX MCP companion (`slopsearx-mcp`) is opt-in via the `mcp` Compose profile and then requires a non-empty `SLOPSEARX_MCP_AUTH_TOKEN`.

Timeouts are configurable: raise `LLM_CALL_TIMEOUT` (idle-timeout seconds, default 120 — e.g. set 300 for reasoning models), and tune `QDRANT_QUERY_TIMEOUT` / `QDRANT_CLIENT_TIMEOUT` if the semantic/Qdrant path needs different bounds.

```bash
cp .env.sample .env   # set LLM_BASE_URL/LLM_API_KEY/LLM_MODEL, BRAVE_API_KEY, API_KEY
docker compose up --build -d
curl http://localhost:8080/health
./groktocrawl scrape https://example.com
```

**Expected success output:** `curl http://localhost:8080/health` returns `{"status":"ok", "checks":{...}}`, and `./groktocrawl scrape https://example.com` prints example.com as markdown. Set `API_KEY` before exposing the API outside a trusted network.

### End-to-end example: an async crawl with a webhook and SSE

This exercises the async job path end to end and shows why the durability boundary matters. The CLI `crawl` command is documented in the [CLI guide](docs/guides/cli.md); the `curl` forms below show the underlying API.

```bash
# Create an async crawl job with a completion webhook
curl -X POST http://localhost:8080/v2/crawl \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","maxDepth":2,"limit":20,"webhook":{"url":"https://your-host.example/hook"}}'
# → {"id":"crawl_<job_id>","status":"processing", ...}

# Poll status until the crawl finishes
curl http://localhost:8080/v2/crawl/crawl_<job_id>
# → {"status":"completed","completed":N,"total":N,"data":[ ... ], ...}

# Or stream live progress inline (stream: true runs the crawl in the response)
curl -N -X POST http://localhost:8080/v2/crawl \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","maxDepth":2,"limit":20,"stream":true}'
# → data: {"type":"page","url":...,"markdown":...}
#   data: {"type":"done","id":...,"status":"completed","pages":N,...}
```

`limit` is honored as a page cap (must be ≥ 1); omit it for an unlimited crawl.

**Expected success output:** the webhook fires a `crawl.started` event before scraping, a `crawl.page` event after each page, and a `crawl.completed` event at the end — each with a unique `webhookId` (omit an `events` filter to receive all of them). The SSE stream emits flat `page` events and a terminal `done` event.

> **Background jobs are best-effort.** Async jobs (crawl, agent, extract, batch-scrape, llmstxt, plan execution) run in-process and are **not restart-safe**: a restart does not resume interrupted work, roll back partial artifacts, or replay undelivered webhooks. After any `agent-svc` restart, reconcile jobs stranded in `processing` — see [Job durability and recovery](docs/guides/deployment.md#job-durability-and-recovery), the [Interrupted Jobs runbook](docs/runbooks/interrupted-jobs.md), and [ADR-0047](docs/adr/0047-defer-restart-safe-execution.md).

## What it provides

| Area | Capabilities |
|---|---|
| Web data | Scrape, batch scrape, map, crawl, parse, browser sessions, and llms.txt generation; the scraper recovers low-yield (card-style) page bodies, warns on partial extractions, and detects bot-challenge pages (e.g. Fastly JS challenges) so barrier content is never served as article content |
| Search and retrieval | SlopSearX search (DuckDuckGo engine falls back to its lite endpoint when the primary frontend is bot-blocked; results best-effort), rich/deep research modes, semantic index, and similarity search; vector-search backend failures surface as errors rather than silent empty results |
| Research | Grounded answers, streaming agent research, plans, sessions, citations, and reusable research memory |
| Operations | Monitors, webhooks, health probes, Prometheus metrics, cache controls, and politeness controls |
| Integrations | Portal UI, Model Context Protocol server, and site adapters for code, publishing, commerce, and security sources |

## Documentation

- [API guide](docs/guides/api.md) — authentication, jobs, SSE, webhooks, examples, and compatibility.
- [CLI guide](docs/guides/cli.md) — commands, global flags, and streaming/JSON output.
- [Deployment and configuration](docs/guides/deployment.md) — services, profiles, configuration, security, and operations.
- [Feature guides](docs/guides/features.md) — scraping, crawl, search, research, sessions, browser, monitors, parse, portal, and MCP.
- [Architecture](docs/architecture.md) — current service and data-flow design.
- [Contributor guide](CONTRIBUTING.md) — contribution intake, local development, tests, API/CLI parity, and ADRs.
- [Roadmap](docs/roadmap.md) — Now / Next / Later priorities.
- [Public surface inventory](docs/reference/public-surface.md) — validated route, CLI, compose, and configuration indexes.

When the stack is running, FastAPI publishes the canonical request/response schema at [Swagger UI](http://localhost:8080/docs) and [OpenAPI JSON](http://localhost:8080/openapi.json). The Markdown guides explain behavior and workflows; OpenAPI is authoritative for wire schemas.

## Architecture at a glance

`agent-svc` is the public API and coordinator. It persists job state in Valkey, calls `scraper-svc` and SlopSearX, uses `semantic-svc`/Qdrant for vector retrieval, and delegates synthesis to an OpenAI-compatible LLM. Supporting services provide browser sessions, document parsing, the portal, scheduled monitors, and MCP access. See the [architecture guide](docs/architecture.md) for the service graph and boundaries.

## Adapters

Site adapters run before the generic scraper pipeline and fall back safely to it when their specialized extraction fails. Supported categories include GitHub, YouTube, Bluesky, Substack, Gutenberg, Greenhouse, AshbyHQ (official posting API with SSR fallback), Shopify, and security/threat-intelligence sources such as NVD, CVE.org, AbuseIPDB, Shodan, VirusTotal, and VulnCheck. Configuration and extension guidance are in the [scraping guide](docs/guides/features.md#scraping-and-adapters).

## Security

Set `API_KEY` for authentication, restrict network exposure, and review outbound proxy and robots/politeness settings before production use. The service emits an `X-Security-Warning` header while authentication is disabled. See [deployment and configuration](docs/guides/deployment.md#security) for the operational baseline.

Self-hosted CI is hardened against untrusted fork PRs: workflow edits on fork PRs fail closed (defense in depth — the platform-level control is authoritative), and deleted-fork PRs are excluded from the self-hosted runner lane. `scripts/enforce-branch-protection.py` gains a read-only `--verify-rulesets` mode that asserts both `main` rulesets are active and diff-clean. See [self-hosted runner fork protection](docs/runbooks/self-hosted-runner-fork-protection.md) and [SECURITY.md](SECURITY.md); the org-level runner-group settings remain deferred with residual risk accepted.

## Status

Core Firecrawl-compatible workflows and GroktoCrawl extensions are actively developed. Review the [roadmap](docs/roadmap.md) for Now / Next / Later priorities, the [changelog](CHANGELOG.md), [ADRs](docs/adr/README.md), and [issues](https://github.com/groktopus/groktocrawl/issues) for change history and planned work. Bugs and feature ideas are filed through the [contribution intake](CONTRIBUTING.md#contribution-intake-and-triage) route.

## Development policy

Merges to `main` require the **Code Quality Gate** and **Runtime Gate** checks to pass and at least one approving review for non-automation changes (stale approvals are dismissed and open review conversations block merge). `dependabot[bot]` skips the review requirement only — it must still pass the required checks; the sole maintainer can merge their own PRs without an approving review (review bypass only — required checks still bind); release-please PRs require a human approving review. See [ADR-0046](docs/adr/0046-enforce-qa-checks-and-review-policy-on-main.md) for the full policy and [Emergency Branch Protection Bypass](docs/runbooks/emergency-branch-protection-bypass.md) for the audited emergency exception path.
