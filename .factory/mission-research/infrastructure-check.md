# Infrastructure Check Report

**Generated:** 2026-07-03
**Machine:** macOS (darwin 25.6.0) — Apple Silicon Mac

---

## 1. Docker Status

### Docker Daemon
**NOT RUNNING.** The `docker` CLI is not installed or not in PATH:
- `docker` command not found in shell PATH
- No binary at `/usr/local/bin/docker`
- No binary at `/opt/homebrew/bin/docker`
- `Docker.app` exists at `/Applications/Docker.app/Contents/` but the daemon appears not to be running

### Running Containers
None. No Docker containers running on this machine.

### Docker Compose
- `docker compose` CLI unavailable (same as Docker)
- **Available services** (from `docker-compose.yml`): cannot be listed via CLI, but the compose file defines these services:
  - `agent-svc` — Main API + agent research loop + crawl engine (port 8000)
  - `scraper-svc` — URL → markdown service (port 8001)
  - `search-svc` — Search fixture for local testing
  - `llm-svc` — LLM fixture for local testing
  - `valkey` — Valkey (Redis-compatible) cache
  - `ofelia` — Job scheduler

### Verdict
**Docker stack cannot be started** without installing/running Docker Desktop (or Docker Engine). This is a blocker for running GroktoCrawl via Docker Compose.

---

## 2. Listening Ports

Ports currently in use (non-system, notable):

| Port | Process | Notes |
|------|---------|-------|
| 8642 | python3.1 (PID 14923) | Hermes agent gateway |
| 8644 | python3.1 (PID 14923) | Hermes agent gateway |
| 8645 | Python (PID 786) | Hermes-related |
| 9100 | node_exporter (PID 797) | System metrics exporter |
| 9999 | Python (PID 801) | Hermes-related |
| 27123 | Obsidian | Obsidian local server |
| 27124 | Obsidian | Obsidian local server |
| 3033 | Fireflies | Local service |
| 6463 | Discord | Discord Rich Presence |
| 48969 | com.nicol... | Unknown app |
| 5000 | ControlCenter | macOS Control Center |
| 7000 | ControlCenter | macOS Control Center |
| 28196 | Stream Deck | Elgato Stream Deck plugin |
| 1835, 1854 | Camera | Camera services |

**GroktoCrawl ports (8000, 8001, 8080, 6379) are NOT in use** — available for the stack once Docker is running.

---

## 3. Valkey / Redis

**Not running.** No `valkey` or `redis` process in the process list.

However, the Python `redis` client library **v8.0.0** is installed — ready for use once the Valkey container is up.

The `.env` file has the default `VALKEY_URL=redis://valkey:6379/0` (commented out — uses default).

---

## 4. Python Environment

| Item | Value |
|------|-------|
| **Version** | Python 3.13.6 |
| **pip** | 25.2 |
| **Location** | `/Library/Frameworks/Python.framework/Versions/3.13/` |
| **Total packages** | 382 |

### Key Installed Packages (GroktoCrawl-relevant)

| Package | Version | Notes |
|---------|---------|-------|
| `groktocrawl` | 0.8.0 | Editable install from `/path/to/groktocrawl` |
| `agent-svc` | 0.7.0 | Editable install (from groktocrawl-wt-agent-query-intelligence dir) |
| `scraper-svc` | 0.7.0 | Editable install (from ~/git/groktocrawl/scraper-svc) |
| `fastapi` | 0.138.0 | Web framework |
| `uvicorn` | 0.49.0 | ASGI server |
| `httpx` | 0.28.1 | HTTP client |
| `httpx-sse` | 0.4.3 | SSE client |
| `sse-starlette` | 3.3.4 | SSE server |
| `aiohttp` | 3.13.3 | Async HTTP |
| `pydantic` | 2.13.2 | Data validation |
| `pydantic-settings` | 2.13.1 | Settings management |
| `redis` | 8.0.0 | Valkey/Redis client |
| `openai` | 2.24.0 | LLM client |
| `anthropic` | 0.94.0 | Anthropic client |
| `litellm` | 1.83.8 | Multi-LLM proxy |
| `mcp` | 1.27.0 | MCP Python SDK |
| `lxml` | 6.1.1 | HTML/XML parsing |
| `readability-lxml` | 0.8.4.1 | Content extraction |
| `beautifulsoup4` | 4.15.0 | HTML parsing |
| `playwright` | 1.61.0 | Browser automation |
| `firecrawl-py` | 4.17.0 | Firecrawl client |
| `graphifyy` | 0.5.5 | Knowledge graph |
| `pytest` | 9.0.3 | Testing |
| `pytest-asyncio` | 1.3.0 | Async test support |
| `pytest-cov` | 7.1.0 | Coverage |
| `ruff` | 0.8.4 | Linter/formatter |
| `mypy` | 1.20.1 | Type checker |
| `rq` | 2.9.1 | Redis Queue (optional worker) |

---

## 5. Environment Configuration (.env)

**File exists** at `/path/to/groktocrawl/.env`

Structure (secrets redacted):
```
BRAVE_API_KEY=placeholder-no-key-configured
LLM_API_KEY=***redacted***
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash
```

Key observations:
- LLM configured for **DeepSeek v4 Flash**
- No search API key configured (`BRAVE_API_KEY` is placeholder)
- All other settings use defaults (Valkey at `redis://valkey:6379/0`, SearXNG at `http://slopsearx:8080`, etc.)
- No `API_KEY` set (no auth on endpoints)
- Politeness protocol, scrape cache, and security adapters all at defaults

---

## 6. Relevant Processes

Notable running processes:

| Process | PID | Notes |
|---------|-----|-------|
| `hermes` (Python) | 15129 | Main Hermes agent process — 5.8% memory |
| `hermes` (Python) | 61482 | Second Hermes process — long-running (since Thu 1AM) |
| `hermes-cli gateway` (Python) | 14923 | Hermes gateway — listening on 8642, 8644 |
| `node_exporter` | 797 | Prometheus metrics exporter on :9100 |
| `droid exec` | 68010 | Current Factory Droid process (this session) |
| `pyright-langserver` (Node) | 21658 | Python language server |
| `yaml-language-server` (Node) | 23283 | YAML language server |
| `docker-langserver` (Node) | 22797 | Docker language server |
| `Stream Deck plugin` (Node) | 7565 | Elgato Stream Deck |
| Obsidian, Discord, Fireflies | various | Desktop apps |

No `valkey`, `redis`, `java`, or Docker-related service processes running.

---

## 7. System Resources

| Resource | Value |
|----------|-------|
| **Total RAM** | 17,179,869,184 bytes ≈ **16 GB** |
| **CPU Cores** | **10** (Apple Silicon) |
| **OS** | macOS 26.6.0 (darwin 25.6.0) |

Adequate resources to run the full GroktoCrawl Docker stack (typically needs 2-4 GB RAM for all services).

---

## 8. Summary & Recommendations

### Can the Docker stack be started?
**No — Docker is not running.** Docker.app exists in `/Applications` but the daemon is not active and the `docker` CLI is not in PATH. You need to:
1. Launch Docker Desktop (or start Docker Engine)
2. Ensure `docker` CLI is in PATH
3. Then: `cd /path/to/groktocrawl && docker compose up --build -d`

### What works without Docker?
- Python 3.13.6 is available with all dependencies installed
- The `groktocrawl` package (0.8.0) is installed as editable
- `mcp` Python SDK 1.27.0 is installed
- `fastapi`, `uvicorn`, `httpx`, `openai` are all available
- Valkey/Redis client library is available (but no server)

### Blockers
1. **Docker not running** — blocks full stack deployment
2. **No Valkey/Redis** — blocks crawl cache, job store, rate limiting (need at least Valkey running)
3. **No SearXNG** — blocks search functionality

### Fallback options
- Individual services (`agent-svc`, `scraper-svc`) could potentially be run directly with Python if their dependencies (Valkey, SearXNG) are provided externally or mocked
- For MCP server development (Phase 5), the `mcp` package is already available — can start implementation without Docker
