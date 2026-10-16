---
name: groktocrawl
description: >-
  Scrape web pages, search the web, crawl sites, map URLs, extract structured
  data, run autonomous research agents, control headless browser sessions, parse
  document files, generate llms.txt files, and schedule change monitors via the
  GroktoCrawl API. Use when the task involves web scraping, web research,
  browser automation, document parsing, or content change detection.
license: MIT
metadata:
  author: groktopus
  version: "1.8.0"
  changelog:
    "1.8.0": "PR #395 Agent-Native Research Platform: session protocol, plan-consent, depth injection, research memory, compact citations, MCP server, structured output for agent"
    "1.7.0": "Update adapter count to 22 (Shopify, ATS added); add Tavily competitive comparison reference"
    "1.6.0": "Agent endpoint SSE streaming; CLI --sync flag; streaming default for agent command"
    "1.5.1": "CLI answer default changed from sync to streaming; --stream flag replaced with --sync (opt-out)"
    "1.4.0": "Add CLI subcommands for monitor (create/list/get/update/delete), parse (file to markdown), and generate-llmstxt (with async polling)"
    "1.3.3": "Add change monitoring section with active job tracking and monitor lifecycle guidance"
    "1.3.2": "Add multi-source research fallback chain (search→scrape→browser→agent) and domain exploration strategy (llms.txt→map→crawl→search site:)"
    "1.3.1": "Add extracting structured data section with prompt guidance and error recovery across commands"
    "1.3.0": "Add full structured extraction workflow with session ID plumbing, browser session lifecycle reference, and multi-step research workflow example"
    "1.2.0": "Add browser session lifecycle guidance, structured extraction examples, search backend config reference, and cross-command chaining patterns"
    "1.1.0": "Add download command, clarify PATH-vs-script CLI path, document search bug fix"
    "1.0.0": "Initial release after SkillOpt Epoch 1 — search pitfalls, multi-step workflows"
---

# GroktoCrawl

Access the GroktoCrawl API — a self-hosted, Firecrawl-compatible web scraping and AI research service. The CLI auto-discovers the server URL from `GROKTOCRAWL_API_URL`, then `FIRECRAWL_API_URL`, then `~/.hermes/.env`, defaulting to `http://localhost:8080`.

The canonical CLI is on PATH (`groktocrawl`).

## Quick Start

```bash
groktocrawl scrape https://example.com
groktocrawl search "raspberry pi 5" --limit 3 --json
groktocrawl agent "What were the key Google I/O 2025 announcements?"  (streaming, default)
groktocrawl agent "Research multimodal models" --sync  (non-streaming, poll for results)
groktocrawl answer "What is the Fed rate?"
groktocrawl answer "How tall is the Burj Khalifa?" --sync  (non-streaming, wait for full answer)
```

## Commands

| Command | Purpose | Example |
|---------|---------|---------|
| scrape | Page to markdown | `groktocrawl scrape <url>` |
| search | Web search | `groktocrawl search <query> --limit 3 --json` |
| download | Binary files | `groktocrawl download <url>` |
| extract | Structured data | `groktocrawl extract <url> --prompt "..."` |
| map | URL discovery | `groktocrawl map <url> --limit 50` |
| crawl | Site crawling | `groktocrawl crawl <url> --max-depth 3` |
| agent | Autonomous research (streaming) | `groktocrawl agent "<prompt>"` |
| answer | Grounded Q&A (streaming) | `groktocrawl answer "<question>"` |
| browser | Headless browser | `groktocrawl browser create --ttl 300` |
| active | List crawl jobs | `groktocrawl active --json` |
| monitor | Manage monitors | `groktocrawl monitor create/list/get/update/delete` |
| parse | Doc file to markdown | `groktocrawl parse <filepath>` |
| generate-llmstxt | Generate llms.txt | `groktocrawl generate-llmstxt <url>` |

## When to Use Which

| Need | Command | Why |
|------|---------|-----|
| Multi-source deep research | `agent "<prompt>"` | Searches, scrapes, and synthesizes deeply |
| Single factual question | `answer "<question>"` | One call, cited answer, streaming by default |
| Single URL content | `scrape <url>` | One fetch, no synthesis |
| Binary files (PDF, EPUB, image) | `download <url>` | Binary content download |
| Search results | `search "<query>"` | Just the search hits |
| Interactive page, JS-heavy SPA | `browser create/exec/destroy` | Headless browser session |
| 20+ site-specific URLs (GitHub, YouTube, Bluesky, Substack, NVD, etc.) | `scrape <url>` | Handled by adapter system, returns rich structured markdown |
| Batch scrape + synthesize | `scrape` then `agent` | Scrape multiple URLs then feed into agent |
| API endpoints, raw JSON | `curl` | No processing needed |
| Discover site URLs | `map <url>` | URL discovery only |
| Full site crawl | `crawl <url>` | Recursive site extraction |
| Structured data from URLs | `extract <url>...` | Schema-guided extraction |
| Document parsing (PDF, DOCX) | `parse <filepath>` | Convert to markdown |

## References

- `references/tavily-comparison.md` — Competitive analysis of Tavily vs GroktoCrawl + SlopSearX (research from 2026-06-23)

## Adapter System

GroktoCrawl has a pluggable **adapter system** for site-specific content handlers — **22 adapters** across code, social/media, CVE, threat intelligence, commerce, and ATS categories. When `scrape <url>` is called, the adapter registry checks for a matching handler before the generic pipeline.

[...]

## Integration with the Generic Pipeline

The generic pipeline now has **7 phases** that run sequentially if earlier phases fail:

1. **Phase 0 — HEAD probe:** Check for `cf-mitigated: challenge` header, binary content-type, >=400 status, or redirects. When shielded, skips Tiers 1-2 entirely.
2. **Tier 1:** Check `/llms.txt` at the site root
3. **Tier 2:** Request with `Accept: text/markdown` header (per-page markdown)
4. **Playwright render** — Full headless browser with navigator spoofing (plugins, languages, hardwareConcurrency, WebGL vendor, viewport randomization ±5px)
5. **FlareSolverr** — Cloudflare JS challenge solving via flare-solverr service (profile-gated, requires `COMPOSE_PROFILES=flare-solverr`)
6. **LLM diagnostic** — LLM analyzes page structure and suggests alternative extraction strategy (diagnostics only, never content enrichment)
7. **Browser-svc** — Fallback browser service for persistent sessions

If an adapter cannot handle a URL, the request falls through to this pipeline.

## Agent-Native Research Platform (v0.11.0+)

PR #395 added an **Agent-Native Research Platform** — transforming GroktoCrawl into a platform where AI agents can conduct multi-step research without accumulating intermediate content in their context windows.

### Session Protocol

Server-side research state management — research results accumulate server-side, agents retrieve only what they need.

| Endpoint | Purpose |
|----------|---------|
| `POST /v2/session/create` | Create a research session, returns `session_id` |
| `POST /v2/session/{id}/step` | Execute a step: `search`, `scrape`, `query`, or `deepen` |
| `POST /v2/session/{id}/export` | Export full session artifact tree |

Step actions:
- **search** — Run a search query, store results in the session
- **scrape** — Fetch a URL, store content
- **query** — LLM query against accumulated session artifacts
- **deepen** — Deepen specific findings (see Depth Injection below)

References tracked by step index + result index. Full content stored server-side; agents request compact summaries.

### Plan-Consent Mode

`POST /v2/agent` accepts `{mode: "plan"}` — generates a research plan before execution. Agents review, modify, and approve the plan before the agent runs.

**Workflow:**
1. `POST /v2/agent` with `{prompt: "...", mode: "plan"}` → returns a research plan with strategy, expected searches, and estimated depth
2. Modify the plan (add/remove search directions, adjust depth)
3. `POST /v2/agent/execute` with the approved plan → runs the research

The CLI's `groktocrawl agent` command defaults to immediate execution (streaming). For plan-consent, use the API directly.

### Depth Injection

The `deepen` session step action — targeted deepening of specific findings within an active session. Rather than re-running a full research pass, inject depth into specific dimensions of existing results.

```
POST /v2/session/{id}/step
{"action": "deepen", "finding_ref": "3.1", "depth_prompt": "Find pricing models"}
```

### Research Memory (Semantic Cache)

Hybrid cache using Valkey (storage) + Qdrant (semantic similarity). Auto-stores agent results on completion; checks cache before running a new research pipeline.

**Freshness classification:**
- `fresh` — recent result, return directly
- `aging` — older result, return but flag staleness
- `stale` — expired, bypass cache and re-run

Bypass cache with `force_fresh=true` on agent requests.

### Compact Citation Model

`POST /v2/agent` and `POST /v2/answer` accept `citation_style: "compact"` — returns citation IDs instead of full source objects. Resolve IDs to full sources via:

```
POST /v2/citations/resolve
{"citation_ids": ["src_1", "src_2", "src_3"]}
```

Useful for agent-native consumers that batch citation resolution.

### Structured Output

`POST /v2/agent` and `POST /v2/answer` accept `output_schema` — a JSON Schema dict that the LLM response must conform to. The LLM client uses strict structured output mode.

```
POST /v2/agent
{"prompt": "...", "output_schema": {"type": "object", "properties": {...}}}
```

### MCP Server

A Model Context Protocol server exposing all GroktoCrawl capabilities through MCP tools. Runs as `mcp-svc` Docker service on port 8002. Provides 20 MCP tools covering:

- `groktocrawl_search` — Web search
- `groktocrawl_scrape` — URL scraping
- `groktocrawl_crawl` — Site crawling
- `groktocrawl_map` — URL discovery
- `groktocrawl_agent` — Autonomous research
- `groktocrawl_answer` — Grounded Q&A
- `groktocrawl_extract` — Structured extraction
- Session tools — Create, step, export sessions
- Monitor tools — Create, list, delete monitors
- Parse tools — Document parsing

Connect MCP servers to Hermes via `~/.hermes/config.yaml`:

```yaml
mcp:
  servers:
    groktocrawl:
      type: http
      url: http://incumbent.example.internal:8002
```

### New CLI Features

The `groktocrawl agent` command now supports:

- `--pyramid` — Write artifact-pyramid output to disk (SSE-buffered)
- `-o/--output-dir` — Output directory for pyramid (implies --pyramid)
- `--include-images` — Collect images from scraped sources

The `groktocrawl answer` command now supports `--pyramid` and `-o/--output-dir`.
