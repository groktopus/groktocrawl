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
  version: "1.2.0"
  changelog:
    "1.2.0": "Add browser session lifecycle guidance, structured extraction examples, search backend config reference, and cross-command chaining patterns"
    "1.1.0": "Add download command, clarify PATH-vs-script CLI path, document search bug fix"
    "1.0.0": "Initial release after SkillOpt Epoch 1 — search pitfalls, multi-step workflows"
---
# groktocrawl

Access the GroktoCrawl API — a self-hosted, Firecrawl-compatible web scraping and AI research service. The CLI auto-discovers the server URL from `GROKTOCRAWL_API_URL`, then `FIRECRAWL_API_URL`, then `~/.hermes/.env`, defaulting to `http://localhost:8080`.

The canonical CLI is on PATH (`groktocrawl`). The skill's `scripts/groktocrawl` is a reference copy that may lag behind the upstream release; when in doubt, check `which groktocrawl` and update from `groktopus/groktocrawl` on GitHub.

## Quick start

```bash
groktocrawl scrape https://example.com
groktocrawl search "raspberry pi 5" --limit 3 --json
groktocrawl agent "What were the key Google I/O 2025 announcements?"
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
| agent | Autonomous research | `groktocrawl agent "<prompt>"` |
| browser | Headless browser | `groktocrawl browser create --ttl 300` |
| active | List crawl jobs | `groktocrawl active --json` |

## When to use which

- **2+ sources needing synthesis** → agent
- **Single URL** → scrape
- **Binary files (PDF, EPUB, image)** → download
- **Search results** → search
- **Interactive page, JS-heavy SPA, YouTube** → browser (also needed when scrape returns short/empty content)
- **Batch scrape + synthesize** → `crawl` or `scrape` multiple URLs, then `agent` to combine
- **Source includes binary files (PDF, image)** → `download` the file, then `agent` to analyze alongside scraped text
- **API endpoints, raw JSON** → curl

## Pitfalls

### Search returns no results — two distinct causes

1. **CLI parsing bug (fixed in v1.1.0):** Old CLI versions had `result.get("data", [])` which grabbed the `{"web":[...]}` dict and silently dropped it. Fix: update from `groktopus/groktocrawl`. Verify with `--json` — if the API returns results but the CLI shows empty, update the CLI.

2. **Search backend not configured.** Verify with `curl -s -X POST $GROKTOCRAWL_API_URL/v2/search -d '{"query":"test","limit":3}'`. If curl returns empty, the server needs a search engine configured. Configure the search backend by setting `SEARXNG_BASE_URL` and related vars in the server's `.env` file — see `docker-compose.yml` for supported search provider config options (SearXNG, Brave, Google).

Fallback when search is persistently empty: use `agent` command (different pipeline), pre-find URLs, or use specific known domains with `site:` filters.

### Scrape returns short or empty content — try browser mode

`scrape` works best on text-heavy, server-rendered sites (Wikipedia, documentation, blogs with static HTML). JS-heavy sites — single-page apps (SPAs), YouTube, modern news sites that fetch content via JavaScript — may return minimal or empty content because they require a headless browser to render.

If a URL seems valid but `scrape` returns under 500 chars, try:

```bash
groktocrawl browser create --ttl 60       # Get a browser session (returns session ID)
groktocrawl browser exec <session> navigate --url <url>   # Load page with JS rendering
groktocrawl browser exec <session> executeScript --script "document.body.innerText"  # Get rendered text
```

Note: `getContent` returns metadata only (url, title, html_length) — not page content. Use `executeScript` to extract rendered text.

**Browser session lifecycle:** Sessions created with `browser create --ttl N` auto-expire after N seconds. For explicit cleanup, the session ID is scoped to your API key — old sessions are garbage-collected server-side. If you're running long extraction pipelines, set `--ttl` high enough (e.g., `--ttl 300` for 5 minutes) to accommodate all navigation and extraction steps.

**Structured extraction from rendered pages:** For extracting specific elements (headings, code blocks, links) rather than raw body text, use targeted CSS selectors:
```bash
groktocrawl browser exec <session> executeScript --script "document.querySelectorAll('h2').map(h => h.textContent)"
groktocrawl browser exec <session> executeScript --script "document.querySelectorAll('pre code').map(c => c.textContent).join('\n')"
```
If `executeScript` also returns empty, the page may require authentication, have a CAPTCHA wall, or load content inside iframes. See `assets/examples.md` for advanced patterns.

### CLI vs reference copy

The active CLI is on PATH. The skill's `scripts/groktocrawl` is a reference copy that may lag upstream. To sync: `curl -L https://github.com/groktopus/groktocrawl/raw/main/groktocrawl -o ~/.local/bin/groktocrawl`.

## Related files

- [references/triggers.md](references/triggers.md) — Decision table
- [assets/examples.md](assets/examples.md) — Extended usage examples
