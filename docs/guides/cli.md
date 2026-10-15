# CLI guide

Run the repository CLI as `./groktocrawl`. It requires `requests`; `uv run ./groktocrawl …` is a convenient isolated setup.

## Global behavior

`--server` selects the agent API base URL. `--json` emits machine-readable output, `--quiet` suppresses nonessential output, `--verbose` writes diagnostics to stderr, and `--dry-run` previews commands that support it. Global options can appear before or after the command.

```bash
./groktocrawl --server http://localhost:8080 --json search "web extraction" --limit 3
```

## Authentication

Set `API_KEY` on the server to require API authentication. The CLI sends `Authorization: Bearer <key>` when `GROKTOCRAWL_API_KEY` is set; this preferred client setting falls back to `API_KEY` when it is not set.

When the server has no `API_KEY`, it intentionally accepts unauthenticated requests and returns an `X-Security-Warning` header. The CLI reports that warning once on stderr, so JSON output on stdout remains valid. This warning means server authentication is disabled, not that a supplied credential is missing or invalid; an enabled server rejects missing or invalid credentials.

`BRAVE_API_KEY` is independent of API authentication. It configures the search provider, and search failures identify it separately.

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

## Evidence budgets and search continuation

`groktocrawl answer "capacitor measurements" --evidence-budget-chars 64000 --sync` selects up to 64,000 source characters from complete bodies; default is 32,000 and allowed range is 256–128,000. JSON and SSE done metadata expose evidence_coverage, including content hashes, offsets and omissions. Model context headers are additional. Text coverage does not establish answer completeness.

`groktocrawl search "capacitor measurements" --limit 20 --page 1 --offset 20 --json` continues within the selected upstream page. JSON exposes coverage and a request-shaped continuation; text mode prints captured/returned counts and the next page/offset for captured results. Omit --stream for continuation. Upstream ordering may change between calls; pagination support and additional upstream results remain unknown. Page/offset currently require fast keyword search without images.

MCP search exposes page/offset and MCP answer exposes evidence_budget_chars with the same API contracts. Session query/deepen can set evidence_budget_chars; query can restrict ref_ids to refs in its own session. Full session evidence remains available through resolve until session TTL/deletion. Agent completion reports its default selection budget; existing include_source_content exports full acquired Markdown.

## Private documents

Use `groktocrawl document create-session|attach|list|show|query|detach` for scoped
parsed-file evidence. See [Private Document Evidence](private-documents.md) for
byte/span bounds, selected document/web refs, ownership and session lifetimes.

The opt-in experimental `research workspace [RUN_ID]` lists or selects retained roots;
`research resume RUN_ID OPERATION --expected-revision DIGEST` performs a named
revision-guarded operation. The protocol may be disabled by feature flags; see the
[API guide](api.md#experimental-research-protocol). See [research workspace](research-workspace.md) for
render/export/evidence, explicit session document context and follow-up preview.

For audited scholarly publication windows, use
`groktocrawl search "retrieval evaluation" --engines openalex --date-from 2026-01-01 --date-to 2026-01-31 --json`.
Bounds are inclusive and may be one-sided. Only non-streaming fast keyword search
is supported; relative `--time-range`, mixed engines and enriched modes are
rejected. JSON output preserves the backend `date_filter` enforcement receipt.
The MCP `search` tool exposes the same `date_from`, `date_to`, `time_range` and
`engines` arguments and applies the same API validation.
