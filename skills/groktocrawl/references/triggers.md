# When to use which groktocrawl command

## Decision table

| Need | Command | Why |
|------|---------|-----|
| Single page content | `scrape <url>` | One fetch, no synthesis |
| Web search, raw results | `search "<query>"` | Just the search hits |
| Multi-source research | `agent "<prompt>"` | Searches, scrapes, and synthesizes |
| Compare A vs B | `agent "Compare A and B"` | Needs reading multiple pages |
| Discover site URLs | `map <url>` | URL discovery only |
| Full site scrape | `crawl <url>` | Recursive site extraction |
| Structured data from URLs | `extract <url>...` | Schema-guided extraction |
| Interactive page (clicks, forms) | `browser create/exec/destroy` | Headless browser session |
| Document parsing (PDF, DOCX) | `curl -X POST .../v2/parse` | No CLI subcommand yet |
| Change monitoring | `curl` to `/v2/monitor` | No CLI subcommand yet |
| Generate llms.txt | `curl` to `/v2/generate-llmstxt` | No CLI subcommand yet |

## Rule of thumb

If the answer needs **2+ sources connected together**, use `agent`.
If you just need content from a single page, use `scrape`.
If you need results from a search engine, use `search`.

## Tool selection order

When fetching content from the web, prefer in this order:

1. **`agent`** — multi-source research that needs search + scrape + synthesis
2. **`scrape`** — any single URL: HTML pages, markdown docs, JSON endpoints, raw text
3. **`search`** — web search with optional content scraping
4. **`curl`** — only for the service's own API endpoints (`/v2/parse`, `/v2/monitor`, etc.) and raw binary downloads (PDFs, images) discovered during research
