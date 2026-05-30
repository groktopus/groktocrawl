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
  version: "1.3.3"
  changelog:
    "1.3.3": "Add change monitoring section with active job tracking and monitor lifecycle guidance"
    "1.3.2": "Add multi-source research fallback chain (search→scrape→browser→agent) and domain exploration strategy (llms.txt→map→crawl→search site:)"
    "1.3.1": "Add extracting structured data section with prompt guidance and error recovery across commands"
    "1.3.0": "Add full structured extraction workflow with session ID plumbing, browser session lifecycle reference, and multi-step research workflow example"
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
| monitor | Change detection | `groktocrawl monitor <url>` |
| active | List crawl jobs | `groktocrawl active --json` |

## When to use which

- **2+ sources needing synthesis** → agent
- **Single URL** → scrape
- **Binary files (PDF, EPUB, image)** → download
- **Search results** → search
- **Interactive page, JS-heavy SPA, YouTube** → browser (also needed when scrape returns short/empty content)
- **Batch scrape + synthesize** → `scrape` multiple URLs (or `crawl` a site), then feed results into `agent` for synthesis
- **Source includes binary files (PDF, image)** → `download` the file to `~/Downloads/`, then reference it in `agent` prompt — the agent can analyze text extracted from the file
- **API endpoints, raw JSON** → curl

**Multi-step workflow example — research with PDF source:** If your PDF URL is `https://example.com/report.pdf`, the `download` command saves the file locally but does not automatically pass it to `agent`. Use a two-step approach:
```bash
groktocrawl download https://example.com/report.pdf    # Saves to current directory or ~/Downloads/
groktocrawl agent "Summarize the key findings from this PDF and compare with the web article at https://other-site.com/blog"
```
The agent will fetch and analyze both sources independently.

## Multi-source research with fallback chain

When researching a topic, no single source type is guaranteed complete. Use this fallback chain when the first approach produces insufficient results:

```bash
# Level 1: Search for leads
groktocrawl search "p5.js 2.0 release WebGPU changes" --limit 5 --json

# Level 2a: If search results are shallow (blogspam, summaries) → scrape primary sources
groktocrawl scrape https://p5js.org/download/

# Level 2b: If scrape returns under 500 chars → try browser for JS-rendered content
SESSION=$(groktocrawl browser create --ttl 120 | grep -o '"[a-f0-9]\{24\}"' | head -1 | tr -d '"')
groktocrawl browser exec "$SESSION" navigate --url "https://p5js.org/download/"
groktocrawl browser exec "$SESSION" executeScript --script "document.body.innerText"

# Level 3: Cross-reference and synthesize all sources
groktocrawl agent "Compare the features from the p5.js release notes at https://p5js.org/download/ with community summaries. What changed in WebGPU support? Are there breaking changes?"
```

**When to escalate:**
- Search returns few results → try `site:domain.com <topic>` or switch search backends
- Scrape returns under 500 chars → browser (JS rendering needed)
- Scrape returns 403/blocked → browser with stealth mode or try curl
- Browser fails → check session TTL has not expired (create fresh if needed)
- Multiple sources give conflicting info → `agent` command to compare and reconcile

**Cross-referencing for verification:** When you need to verify a specific claim across multiple sources, do not rely on a single source type. Fetch the claim from an official source (scrape/browser), cross-reference with community/analysis sources (search), and feed both into an `agent` for comparison. This is especially important for technical specifications, version changes, and breaking changes.

## Extracting structured data

The `extract` command (`groktocrawl extract <url> --prompt "..."`) uses the LLM to return structured data from a page. It works best on static HTML pages with clear content patterns.

**Prompt tips:** Describe what you want as a comma-separated list of fields. The LLM extracts whatever it finds, so be specific about what to look for and what to ignore. For example:

```bash
groktocrawl extract https://example.com/products --prompt "product names, prices, and whether each is in stock"
```

**Output:** Returns a JSON object or array with the fields you requested. If the prompt is vague, the output may be freeform text instead of structured fields.

**When extract fails:**
- The page is JS-rendered (SPA) → extract reads the raw HTML before JS executes. Use the browser pipeline instead: create a session, navigate, then use `executeScript` with targeted CSS selectors (see Browser section above).
- The prompt is too broad → narrow the focus to specific fields. If the page has many products, try extracting from a single section first.
- The page is behind auth or returns 403 → `extract` cannot access authenticated content. Try `browser` with cookies or ensure the page is publicly accessible.

**When to use extract vs browser + executeScript:**
- `extract` — quick extraction from static HTML when you know roughly what you want
- `browser + executeScript` — JS-rendered pages, precise CSS selectors, when you need control over the extraction logic

## Domain exploration strategy

When exploring an unfamiliar website for comprehensive coverage, use this systematic approach rather than one-off `scrape` calls:

```bash
# Phase 1: Discover the site's surface
# Try llms.txt first (agent-friendly docs), then sitemap.xml
curl -sL https://example.com/llms.txt
# OR
groktocrawl scrape https://example.com/sitemap.xml

# Phase 2: Breadth-first URL inventory with map
groktocrawl map https://example.com/docs --limit 100

# Phase 3: Depth-first content extraction with crawl
# Pick a subpath from the map output
groktocrawl crawl https://example.com/docs/api --max-depth 2 --limit 50

# Phase 4: Gap detection with search site: prefix
groktocrawl search "site:example.com tutorial" --limit 5 --json
```

**Deciding between map, crawl, and search:**

| Goal | Tool | Why |
|------|------|-----|
| List all URLs on a site | `map` | Fast, breadth-first, returns URLs only — good for inventorying |
| Extract full page content from a section | `crawl` | Depth-first, returns markdown — good for documentation, blogs |
| Find pages about a specific topic on the site | `search site:` | Query-driven — good for targeted discovery |
| Find the agent-friendly entry point | `curl /llms.txt` | Fastest — one request, full site map in structured text |

**Depth strategy:**
- `--max-depth 1`: Single level only (page + its immediate links). Use for landing pages, index pages.
- `--max-depth 2`: Page + one level of links. Use for documentation sites with a table of contents.
- `--max-depth 3+`: Full subsite crawl. Use for deep hierarchies. Always pair with `--limit` to bound the crawl.
- No limit: Only for small, well-understood sites (under ~200 pages).

**When map + crawl together:** Start with `map` to understand site structure (breadth-first, low cost), then `crawl` specific subpaths for content (depth-first, higher cost). This avoids crawling irrelevant sections.

## Change monitoring

Track when a page's content changes — useful for documentation updates, blog posts, pricing pages, or any URL whose content you want to watch.

```bash
# Set up a monitor on a URL
groktocrawl monitor https://example.com/docs --interval daily

# List all active monitors and crawl jobs
groktocrawl active --json
```

**Output of `active`:** Returns a JSON array of job objects. Each job has an `id`, `url`, `status` (one of `processing`, `completed`, `failed`), and timestamps. If a crawl or agent job failed partway through, it may still appear here with partial results despite the client timing out.

**Monitor lifecycle:**
- **Setup:** `groktocrawl monitor <url>` registers a URL for periodic change detection. The `--interval` flag controls check frequency (e.g., `daily`, `weekly`, `hourly`).
- **Checking:** Use `groktocrawl active --json` to list all active monitors and their last-check status. Each entry shows `url`, `status`, `last_checked`, and `changed` (boolean).
- **Results:** When a change is detected, the monitor records the diff. Check the `active` output for `changed: true` entries, then re-scrape the URL for current content.
- **Teardown:** There is no built-in `monitor remove` command. To stop monitoring, note the job ID from `active` output and contact the server admin or restart the service.

**When to use monitor vs active:**
- `monitor` — set up a recurring check on a specific URL
- `active` — inspect all running jobs (crawls, agents, monitors) and their statuses

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

**Browser session lifecycle:**

- **Default TTL:** If `--ttl` is not specified, sessions default to a 60-second lifetime.
- **Setting TTL:** Use `--ttl N` where N is seconds. For long pipelines, `--ttl 300` (5 minutes) is recommended. Sessions are scoped to your API key; old sessions are garbage-collected server-side.
- **Checking status:** There is no built-in session status command. If you need to verify a session is still alive, attempt a `navigate` — a valid session returns page content; an expired session returns an error. If a session expires mid-pipeline, create a new one with `browser create --ttl N` and re-navigate.
- **Session ID persistence:** The session ID returned by `browser create` is a hex string. Save it to a variable (`SESSION=$(groktocrawl browser create --ttl 300 | ...)`) for reuse across multiple `browser exec` commands. The session survives as long as its TTL — it does not persist across server restarts.
- **Idle timeouts:** The TTL is a total lifetime, not an idle timeout. Even if you're actively sending commands, the session expires after N seconds from creation. For pipelines exceeding the TTL, split the work across multiple shorter sessions or set a generous TTL upfront.

**Structured extraction from rendered pages — full workflow:** For extracting specific elements (headings, code blocks, links) rather than raw body text, use targeted CSS selectors. Here is the complete workflow with session ID plumbing:

```bash
# 1. Create a session with sufficient TTL
SESSION=$(groktocrawl browser create --ttl 120 | grep -o '"[a-f0-9]\{24\}"' | head -1 | tr -d '"')

# 2. Navigate to the page
groktocrawl browser exec "$SESSION" navigate --url "https://example.com/docs"

# 3. Extract h2 headings (returns JSON array)
groktocrawl browser exec "$SESSION" executeScript --script "document.querySelectorAll('h2').map(h => h.textContent)"

# 4. Extract code blocks (returns joined text)
groktocrawl browser exec "$SESSION" executeScript --script "document.querySelectorAll('pre code').map(c => c.textContent).join('\n')"

# 5. Extract all links with hrefs
groktocrawl browser exec "$SESSION" executeScript --script "Array.from(document.querySelectorAll('a[href]')).map(a => ({text: a.textContent, href: a.href}))"
```

**Output format:** `executeScript` returns the JS expression's result as a JSON string. Arrays of strings come back as `["item1", "item2"]`. Objects come back as `{"key": "value"}`. If the result is a single value (string, number), it's returned directly.

If `executeScript` also returns empty, check for rendered elements with `document.body.innerText.length` to verify the page actually loaded. See `assets/examples.md` for advanced patterns.

### Error recovery across commands

GroktoCrawl commands can fail in three common ways. Here is how to handle each:

| Error type | Symptoms | Recovery |
|------------|----------|----------|
| **Network / HTTP errors** | `curl` returns 4xx or 5xx; CLI shows connection refused or timeout | Check the server is running (`docker ps`), the URL is reachable (`curl -I <url>`), and the API key is set in `.env`. Retry with backoff (wait 5s between attempts). |
| **Content quality failures** | Scrape returns under 500 chars; search returns empty; extract returns null | These are the per-command content issues documented above. Switch modes: scrape→browser, search→agent or check backend, extract→browser executeScript. |
| **Timed-out operations** | Crawl hangs mid-way; agent does not return after several minutes | Set explicit timeouts: for crawl reduce `--max-depth` or add `--limit` to bound the site surface. For agent, the server enforces a timeout — if it consistently fails, check `LLM_TIMEOUT` in the server's `.env`. |

**General approach:** If a command fails, try the same request via `curl` against the API endpoint to isolate CLI-vs-server causes. If `curl` also fails, the issue is server-side (config, network, resources). If `curl` succeeds but the CLI fails, report a CLI bug.

If a crawl or agent job fails partway through, the server may have partial results. Use `groktocrawl active --json` to check for in-progress or completed jobs that may have finished despite the client timing out.

### CLI vs reference copy

The active CLI is on PATH. The skill's `scripts/groktocrawl` is a reference copy that may lag upstream. To sync: `curl -L https://github.com/groktopus/groktocrawl/raw/main/groktocrawl -o ~/.local/bin/groktocrawl`.

## Related files

- [references/triggers.md](references/triggers.md) — Decision table
- [assets/examples.md](assets/examples.md) — Extended usage examples
