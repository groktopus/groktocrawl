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
  version: "1.0.0"
---
# groktocrawl

Access the GroktoCrawl API — a self-hosted, Firecrawl-compatible web scraping and AI research service. The CLI auto-discovers the server URL from `GROKTOCRAWL_API_URL`, then `FIRECRAWL_API_URL`, then `~/.hermes/.env`, defaulting to `http://localhost:8080`.

## Quick start

```bash
# Scrape a page
scripts/groktocrawl scrape https://example.com

# Search the web
scripts/groktocrawl search "raspberry pi 5" --limit 3 --json

# Run autonomous research (searches, scrapes, synthesizes)
scripts/groktocrawl agent "What were the key Google I/O 2025 announcements?"
```

All commands accept `--server <url>` to override the server endpoint, `--json` for machine-readable output, and `--dry-run` to preview without executing.

## Commands

### scrape — Single page to markdown
```
scripts/groktocrawl scrape <url> [--format markdown links json] [--timeout ms] [-o file]
```

### search — Web search with content
```
scripts/groktocrawl search <query> [--limit N] [--scrape-results] [--json]
```

### map — URL discovery
```
scripts/groktocrawl map <url> [--limit N] [--search term]
```

### crawl — Site crawling
```
scripts/groktocrawl crawl <url> [--limit N] [--max-depth N] [--no-poll]
```
Without `--no-poll`, polls until the crawl completes. With `--json`, returns structured page data.

### agent — Autonomous research
```
scripts/groktocrawl agent "<prompt>" [--urls <url>...] [--no-poll]
```
Searches the web, scrapes relevant pages, and synthesizes an answer using the configured LLM. Polls for completion unless `--no-poll` is passed.

### extract — Structured data from URLs
```
scripts/groktocrawl extract <url> [<url>...] [--prompt "extraction prompt"] [--no-poll]
```

### browser — Headless browser sessions
```
scripts/groktocrawl browser create --ttl 300
scripts/groktocrawl browser exec <id> navigate --url <url>
scripts/groktocrawl browser exec <id> click --selector "#btn"
scripts/groktocrawl browser exec <id> screenshot
scripts/groktocrawl browser list
scripts/groktocrawl browser destroy <id>
```
Actions: navigate, click, type, screenshot, scroll, wait, getContent, executeScript

### active — List active crawl jobs
```
scripts/groktocrawl active [--json]
```

## Output handling

- Default: human-readable text to stdout
- `--json`: structured JSON to stdout (pipe to `jq` or tools)
- Errors go to stderr
- Exit code 0 on success, 1 on error

## Server configuration

The CLI resolves the server URL in this order:
1. `--server <url>` flag
2. `GROKTOCRAWL_API_URL` environment variable
3. `FIRECRAWL_API_URL` environment variable (backward compat)
4. `~/.hermes/.env` file (checks both var names)
5. Default: `http://localhost:8080`

## Prerequisites

The CLI requires `requests` (Python package). Install with `pip install requests` if not already present. Python 3.8+.

## When to use which command

See [references/triggers.md](references/triggers.md) for a full decision table. The short version:

- **2+ sources needing synthesis** → `agent`
- **Single URL** → `scrape`
- **Search results** → `search`
- **Interactive page (clicks, forms)** → `browser`
- **API endpoints, binary downloads** → `curl`

## Pitfalls

### Agent auto-search can pick up irrelevant sources

The `agent` command's auto-search may return low-quality or unrelated results for niche or domain-specific topics. When results look off:
1. `search` to find specific quality URLs
2. `scrape` each one
3. Synthesize the results yourself

For academic/research topics, pre-filter with `site:.edu`, `site:.gov`, or `site:arxiv.org` in your agent prompt or search query.

### scrape fails on non-HTML content

`scrape` is optimized for HTML pages. It will not produce useful output for XML sitemaps, RSS feeds, or raw JSON APIs. Use `curl` directly for those:

```bash
curl -sL "https://example.com/sitemap-posts.xml"
```

### Binary content returns a download, not markdown

The scraper auto-detects PDFs, EPUBs, and images by Content-Type and returns a download payload (filename, size, content_type). Use `curl` with a real browser User-Agent to download binary files:

```bash
curl -L -A "Mozilla/5.0 ..." -H "Referer: <origin>" -o file.pdf "<url>"
```

### SPA-like symptoms may just be stale URLs

Substack 404 pages render as a large SPA app shell — same appearance as a rendering failure (no `<article>`, thin body, publication-name-only title). Before debugging scraping, verify the URL is current using the publication's archive at `/api/v1/archive?limit=5`. If the slug isn't in the archive, the URL is dead.

## Related files

- [references/triggers.md](references/triggers.md) — Decision table: which command when
- [assets/examples.md](assets/examples.md) — Extended usage examples for every subcommand
