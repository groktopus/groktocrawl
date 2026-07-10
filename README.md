# GroktoCrawl

GroktoCrawl is a self-hosted, MIT-licensed web data platform compatible with the Firecrawl v2 API surface. It combines scraping, crawl and map jobs, search, structured extraction, browser automation, monitors, semantic retrieval, an autonomous research agent, and an MCP server in one Docker deployment.

## Start here

```bash
cp .env.sample .env
docker compose --profile fixture up --build -d
curl http://localhost:8080/health
./groktocrawl scrape https://example.com
```

The `fixture` profile starts a local LLM fixture and test sites. For production, omit that profile and configure an OpenAI-compatible provider plus `BRAVE_API_KEY` for web search. Set `API_KEY` before exposing the API outside a trusted network.

## What it provides

| Area | Capabilities |
|---|---|
| Web data | Scrape, batch scrape, map, crawl, parse, browser sessions, and llms.txt generation |
| Search and retrieval | SlopSearX search, rich/deep research modes, semantic index, and similarity search |
| Research | Grounded answers, streaming agent research, plans, sessions, citations, and reusable research memory |
| Operations | Monitors, webhooks, health probes, Prometheus metrics, cache controls, and politeness controls |
| Integrations | Portal UI, Model Context Protocol server, and site adapters for code, publishing, commerce, and security sources |

## Documentation

- [API guide](docs/guides/api.md) — authentication, jobs, SSE, webhooks, examples, and compatibility.
- [CLI guide](docs/guides/cli.md) — commands, global flags, and streaming/JSON output.
- [Deployment and configuration](docs/guides/deployment.md) — services, profiles, configuration, security, and operations.
- [Feature guides](docs/guides/features.md) — scraping, crawl, search, research, sessions, browser, monitors, parse, portal, and MCP.
- [Architecture](docs/architecture.md) — current service and data-flow design.
- [Contributor guide](CONTRIBUTING.md) — local development, tests, API/CLI parity, and ADRs.
- [Public surface inventory](docs/reference/public-surface.md) — validated route, CLI, compose, and configuration indexes.

When the stack is running, FastAPI publishes the canonical request/response schema at [Swagger UI](http://localhost:8080/docs) and [OpenAPI JSON](http://localhost:8080/openapi.json). The Markdown guides explain behavior and workflows; OpenAPI is authoritative for wire schemas.

## Architecture at a glance

`agent-svc` is the public API and coordinator. It persists job state in Valkey, calls `scraper-svc` and SlopSearX, uses `semantic-svc`/Qdrant for vector retrieval, and delegates synthesis to an OpenAI-compatible LLM. Supporting services provide browser sessions, document parsing, the portal, scheduled monitors, and MCP access. See the [architecture guide](docs/architecture.md) for the service graph and boundaries.

## Adapters

Site adapters run before the generic scraper pipeline and fall back safely to it when their specialized extraction fails. Supported categories include GitHub, YouTube, Bluesky, Substack, Gutenberg, Greenhouse, AshbyHQ, Shopify, and security/threat-intelligence sources such as NVD, CVE.org, AbuseIPDB, Shodan, VirusTotal, and VulnCheck. Configuration and extension guidance are in the [scraping guide](docs/guides/features.md#scraping-and-adapters).

## Security

Set `API_KEY` for authentication, restrict network exposure, and review outbound proxy and robots/politeness settings before production use. The service emits an `X-Security-Warning` header while authentication is disabled. See [deployment and configuration](docs/guides/deployment.md#security) for the operational baseline.

## Status

Core Firecrawl-compatible workflows and GroktoCrawl extensions are actively developed. Review the [changelog](CHANGELOG.md), [ADRs](docs/adr/README.md), and [issues](https://github.com/groktopus/groktocrawl/issues) for change history and planned work.
