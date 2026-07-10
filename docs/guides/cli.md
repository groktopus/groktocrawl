# CLI guide

Run the repository CLI as `./groktocrawl`. It requires `requests`; `uv run ./groktocrawl …` is a convenient isolated setup.

## Global behavior

`--server` selects the agent API base URL. `--json` emits machine-readable output, `--quiet` suppresses nonessential output, `--verbose` writes diagnostics to stderr, and `--dry-run` previews commands that support it. Global options can appear before or after the command.

```bash
./groktocrawl --server http://localhost:8080 --json search "web extraction" --limit 3
```

## Commands

| Command | Purpose |
|---|---|
| `scrape` | Scrape one URL; choose formats, contents options, output file, or image download. |
| `search` | Search with sources, categories, contents, rich/deep modes, schema, prompt, or SSE. |
| `map` | Discover URLs for a site. |
| `crawl` | Create and optionally poll a crawl; set depth, paths, format, and page limits. |
| `agent`, `answer` | Run streaming research or grounded Q&A; `--sync` disables streaming. |
| `extract`, `batch-scrape`, `generate-llmstxt` | Start or inspect asynchronous extraction jobs. |
| `browser` | `create`, `exec`, `list`, and `destroy` browser sessions. |
| `monitor` | `create`, `list`, `get`, `run`, `update`, and `delete` monitors. |
| `parse`, `parse-upload` | Parse a local document or upload first for the two-step parse API. |
| `enrich`, `find-similar` | Enrich structured items or find pages similar to a URL. |
| `active` | List active crawl jobs. |
| `download` | Save binary content, optionally extracting text where supported. |

The checked [CLI inventory](../reference/public-surface.md#cli-commands) tracks top-level command additions. Use `./groktocrawl <command> --help` for authoritative flags and subcommand requirements.

## Examples

```bash
./groktocrawl scrape https://example.com --format markdown links
./groktocrawl search "LLM extraction" --sources news --search-type rich
./groktocrawl crawl https://example.com --max-depth 2 --include-paths /docs/*
./groktocrawl agent "Compare the latest releases" --search-type deep
./groktocrawl answer "What is this site about?" --num-sources 3
./groktocrawl monitor create https://example.com --schedule '0 */6 * * *'
```

Commands that start asynchronous work print a job identifier unless polling is enabled. The API guide explains the matching status, cancellation, webhook, and streaming behavior.
