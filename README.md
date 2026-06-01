# GroktoCrawl

**Self-hosted, API-compatible Firecrawl alternative with Agent endpoint. MIT licensed. One `docker compose up` and you're running.**

GroktoCrawl implements the Firecrawl v2 API surface — scrape, search, map, crawl, extract, browser sessions, monitors, and the **Agent** endpoint (autonomous web research) — without the closed-source dependencies. Runs entirely in Docker on your own hardware. Bring your own LLM or use the built-in fixtures.

## Quick Start

```bash
cp .env.sample .env
docker compose up --build -d
```

Eight containers start. The stack includes SearXNG for real web search, a smart scraper, and an Ofelia-scheduled monitor system.

```bash
# CLI
./groktocrawl scrape https://example.com
./groktocrawl search "raspberry pi 5" --limit 3
./groktocrawl agent "What were the key Google I/O 2025 announcements?"

# Or raw curl
curl http://localhost:8080/health
curl -X POST http://localhost:8080/v2/scrape -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

## Production Setup

Edit `.env` to point at a real LLM:

```env
# DeepSeek
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash

# OpenAI
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# Ollama (local)
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=llama3.2
```

## Architecture

```mermaid
flowchart TD
    subgraph compose["docker-compose.yml"]
        valkey[("valkey<br/>(queue + storage)")]
        searxng["searxng<br/>(web search)"]
        scraper("scraper-svc<br/>(smart fetch)")
        browser["browser-svc<br/>(Playwright sessions)"]
        agent("agent-svc<br/>(FastAPI + workers)")
        ofelia["ofelia<br/>(cron scheduler)"]

        valkey --- agent
        searxng --- agent
        scraper --- agent
        browser --- agent
        ofelia -.->|docker exec| agent
    end
    llm_provider("LLM Provider<br/>(DeepSeek / OpenAI / Ollama)")
    llm_provider -.->|LLM_BASE_URL| agent

    style valkey fill:#ffe0b0
    style searxng fill:#b0d4ff
    style scraper fill:#b0ffb0
    style browser fill:#d4b0ff
    style agent fill:#ffb0b0
    style ofelia fill:#b0b0b0
```

The scraper uses a **three-tier strategy**: check `/llms.txt` first, try `Accept: text/markdown` second, render with Playwright third.

## CLI

`groktocrawl` is a CLI tool in the repo root. It needs `requests`.

If you want to avoid installing dependencies into your global Python, use a repo-local `uv` environment:

```bash
uv venv
uv pip install requests
uv run ./groktocrawl scrape https://example.com
```

To expose a global `groktocrawl` command while keeping dependencies isolated, create a small wrapper somewhere on your `PATH`:

```bash
cat > ~/.local/bin/groktocrawl <<'EOF'
#!/bin/sh
cd "$HOME/groktocrawl" || exit 1
exec uv run ./groktocrawl "$@"
EOF
chmod +x ~/.local/bin/groktocrawl
```

Or install `requests` into the Python that runs the script:

```bash
python3 -m pip install requests
```

```bash
./groktocrawl scrape <url>                  # Scrape a page to markdown
./groktocrawl search <query> --limit 5      # Search the web
./groktocrawl map <url> --limit 100         # Discover URLs on a site
./groktocrawl crawl <url> --max-depth 2     # Crawl a website
./groktocrawl agent "<prompt>"              # Autonomous research agent
./groktocrawl --json --server <url> <cmd>   # JSON output, custom server
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v2/scrape` | Scrape a single URL to clean markdown |
| POST | `/v2/agent` | Start an autonomous research agent |
| GET | `/v2/agent/:jobId` | Get agent job status and results |
| DELETE | `/v2/agent/:jobId` | Cancel an agent job |
| POST | `/v2/extract` | Extract structured data from URLs (with schema) |
| GET | `/v2/extract/:jobId` | Get extract status and results |
| POST | `/v2/crawl` | Crawl a website |
| GET | `/v2/crawl/:jobId` | Get crawl status |
| DELETE | `/v2/crawl/:jobId` | Cancel a crawl |
| POST | `/v2/batch/scrape` | Scrape multiple URLs |
| POST | `/v2/search` | Search the web with content |
| POST | `/v2/map` | Discover URLs on a site |
| POST | `/v2/parse` | Upload a file (PDF, DOCX, PPTX, XLSX) and get markdown back |
| POST | `/v2/browser` | Create a headless browser session |
| GET | `/v2/browser` | List active browser sessions |
| POST | `/v2/browser/:id/execute` | Execute action (navigate, click, screenshot, etc.) |
| DELETE | `/v2/browser/:id` | Destroy a browser session |
| POST | `/v2/monitor` | Create a scheduled change monitor |
| GET | `/v2/monitor` | List all monitors |
| GET | `/v2/monitor/:id` | Get monitor status and history |
| PATCH | `/v2/monitor/:id` | Update monitor config |
| DELETE | `/v2/monitor/:id` | Delete a monitor |
| POST | `/v2/generate-llmstxt` | Generate an llms.txt file for a website |
| GET | `/v2/generate-llmstxt/:jobId` | Get generation status and result |

All Firecrawl v2 API-compatible in request/response shape.

### Agent endpoint

The `POST /v2/agent` endpoint accepts an optional `model` field to override the environment-configured LLM on a per-request basis:

```json
{
  "prompt": "Research the latest AI safety papers",
  "model": "gpt-4o"
}
```

When `model` is omitted or set to `"default"`, the `LLM_MODEL` from `.env` is used. This is useful for routing simple lookups to a cheaper model and complex research to a more capable one.

The agent is powered by a **determined research prompt** that evaluates source quality, synthesizes across multiple pages, detects contradictions, and cites sources by URL. It does not fabricate information — if the available sources don't answer the question, it says so and suggests what would be needed.

## OpenAPI / Swagger Docs

Interactive API documentation is available when the stack is running:

- **Swagger UI**: [`http://localhost:8080/docs`](http://localhost:8080/docs)
- **Raw OpenAPI spec**: [`http://localhost:8080/openapi.json`](http://localhost:8080/openapi.json)

The spec is auto-generated by FastAPI from the route handlers and Pydantic models — always up to date with the running code. All 17+ endpoints with request/response schemas are documented.

## Comparison to Firecrawl

| Feature | Firecrawl Cloud | Firecrawl Self-Hosted | GroktoCrawl |
|---------|----------------|----------------------|-------------|
| Scrape / Crawl / Map / Search | ✅ | ✅ | ✅ |
| Agent endpoint | ✅ | ❌ (closed-source) | ✅ |
| Extract (schema-based) | ✅ | ❌ (closed-source) | ✅ |
| Browser sessions | ✅ | ❌ (closed-source) | ✅ |
| Scheduled monitors | ✅ | ❌ (closed-source) | ✅ |
| Parse (PDF, DOCX) | ✅ | ✅ | ✅ |
| Generate llms.txt | ❌ (deprecated in v2) | ❌ (deprecated in v2) | ✅ |
| Webhook delivery | ✅ | ✅ | ✅ |
| License | Proprietary | AGPL-3.0 | **MIT** |
| Self-contained Docker | ✅ | ⚠️ requires Supabase, Stripe | **✅ one file** |
| LLM integration | Built-in | Requires API key | **BYO or fixture** |

## AgentSkills Compatibility

GroktoCrawl ships as an [AgentSkills](https://agentskills.io)-compatible skill at `skills/groktocrawl/`. Any agent that supports the AgentSkills format (Claude Code, Cursor, etc.) can load it:

```
skills/groktocrawl/
├── SKILL.md                  # Metadata + instructions
├── scripts/groktocrawl       # CLI — all endpoints
├── references/triggers.md    # When to use which command
└── assets/examples.md        # Usage examples
```

The skill bundles the CLI directly — no additional setup required beyond having the repo on disk.

## Hermes Agent Considerations

If you use Hermes Agent, GroktoCrawl replaces the built-in `web_search` and `web_extract` tools with more capable alternatives. To avoid competition between tools:

### Disable the `web` toolset

Remove `web` from `default_toolsets` and `platform_toolsets.cli` in `~/.hermes/config.yaml`:

```yaml
# Before
default_toolsets:
  - terminal
  - file
  - web              # ← remove

# After
default_toolsets:
  - terminal
  - file
```

This removes `web_search` and `web_extract` from your agent's toolset. All web tasks will route through `groktocrawl` instead.

### Install the CLI

The CLI is at `groktocrawl` in the repo root. Copy it to your PATH:

```bash
cp groktocrawl ~/.local/bin/
```

### Install the AgentSkills skill

The bundled skill at `skills/groktocrawl/` follows the [AgentSkills](https://agentskills.io) spec. Symlink it into your Hermes skills directory:

```bash
ln -sf "$PWD/skills/groktocrawl" ~/.hermes/skills/
```

Then load it in-session with `/skill groktocrawl` or preload it via `hermes -s groktocrawl`.

### Environment variables

The CLI discovers the server in this order:
1. `--server <url>` flag
2. `GROKTOCRAWL_API_URL` env var
3. `FIRECRAWL_API_URL` env var (backward compat)
4. `~/.hermes/.env` file
5. Default: `http://localhost:8080`

Add to `~/.hermes/.env` if your instance runs elsewhere:
```env
GROKTOCRAWL_API_URL=http://localhost:8080
```

## Security

### API Authentication (recommended for production)

Set `API_KEY` in your `.env` file to enable bearer token authentication:

```env
API_KEY=sk-your-secret-key-here
```

Once set, every API call must include an `Authorization` or `X-API-Key` header:

```bash
curl -X POST http://localhost:8080/v2/scrape \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-secret-key-here" \
  -d '{"url": "https://example.com"}'

# Or via CLI:
groktocrawl --api-key sk-your-secret-key-here scrape https://example.com
```

When **no** `API_KEY` is configured, the API is fully open (backward
compatible). Each response includes an `X-Security-Warning` header and
the `/health` endpoint adds a `security` field to warn callers.

### Private Network Protection

The built-in browser and scraper services block navigation to private IPs
(RFC 1918), loopback addresses, cloud metadata endpoints, and the Docker
host machine. This prevents SSRF-based pivoting through the headless
browser. The blocklist applies to both direct URLs and resolved hostnames
(DNS rebinding protection).

### Service Architecture

Only the **agent API** (port `8080`) is exposed to the host. Internal
services (`browser-svc`, `scraper-svc`, `parse-svc`) are reachable only
via Docker internal DNS — they do not publish host ports. All requests
route through the agent API.

### Reporting Vulnerabilities

See [SECURITY.md](SECURITY.md) for our disclosure policy and how to
privately report security issues.

## Project Status

Active development. All core Firecrawl v2 API endpoints implemented and integration-tested. See [issues](https://github.com/groktopus/groktocrawl/issues) for upcoming features. Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
