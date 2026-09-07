# Artifact Pyramid CLI Output Mode

* Status: proposed
* Deciders: magnus, jasper
* Date: 2026-06-08
* ADR: 0024

Technical Story: The groktocrawl CLI currently returns flat output — raw markdown (scrape), streaming prose (agent/answer), or flat JSON (search --json). There is no structured, progressive-disclosure output format suitable for consumption by downstream AI agents that need to navigate findings, dimensions, and source material independently.

## Context and Problem Statement

GroktoCrawl CLI output currently serves two modes:

| Mode | Output Shape | Consumer |
|---|---|---|
| Human | Streaming text, markdown, formatted listings | Terminal user reading inline |
| Machine | `--json` flat dict | Scripts needing raw data |

Neither mode is suitable for agent-to-agent handoff. An agent consuming the output of `groktocrawl agent "Research X"` gets a wall of text or a flat JSON blob — no structural navigation, no evidence chain, no way to consume only the layer it needs.

The Artifact Pyramid pattern (progressive disclosure across three layers of increasing depth) solves this: L1 summary (key findings), L2 analysis dimensions (per-angle groupings), L3 dossiers (full source material). Each layer is independently consumable by agents with different needs.

## Decision Drivers

* Must be **CLI-side only** — no server-side endpoint changes. The existing agent/answer endpoints are unchanged; the CLI transforms their output client-side
* Must be **opt-in** — existing streaming/prose output remains the default. Pyramid mode is a flag (`--pyramid`)
* Must produce **artifact-pyramid compliant** directory output per the [artifact-pyramids spec](https://github.com/groktopus/artifact-pyramids)
* Must output only an **absolute filesystem path** to the pyramid root — the calling agent captures this path and navigates the pyramid itself
* Must support both **agent** (multi-source research) and **answer** (grounded Q&A) commands
* Must be **self-contained** — no additional dependencies, no server-side LLM calls for the pyramid transformation
* Output directory must be **immutable for L3 files** — once written, source dossiers are never modified. Root and summary files are overwritten on re-run

## Considered Options

### A. CLI-side transformation with directory output, SSE-buffered *(chosen)*

The CLI runs the SSE stream as normal (fastest path — inline processing, no poll loop) but buffers tokens instead of printing them to stdout. Progress goes to stderr. On stream completion, writes the pyramid directory and prints the absolute path to stdout.

Pyramid mode requests the opt-in `include_source_content` agent response field.
The ordinary agent response remains compact: `sources` contains URL strings and
`source_details` contains fetch metadata only. When the export field is enabled,
the completed response additionally contains `source_contents`, a URL-to-Markdown
mapping for successfully acquired sources. The CLI combines that mapping with the
metadata to write L3 dossiers and fails clearly if a consulted source body is
unavailable; it never writes `(no content available)` as a consulted dossier.

**Positive:**
- Zero server-side changes — works with the existing API
- Uses the fastest execution path (inline SSE vs async job polling)
- Progress visible on stderr without contaminating stdout
- Directory output is inspectable by both humans and agents
- L3 files carry YAML frontmatter for source metadata (url, fetch timestamp, word count)

**Negative:**
- L2 files are simple stubs (grouped by domain, not full dimensional synthesis)
- No real-time token output on stdout (stderr progress only)
- Directory I/O is slower than streaming to stdout
- No automatic cleanup of old pyramids

### B. CLI-side transformation, sync-enforced

Pyramid mode forces `--sync` (non-streaming, poll-based). The CLI waits for the full result, writes the pyramid, prints the path.

**Positive:**
- Simpler code — no SSE buffering needed
- Clear separation: streaming is for humans, pyramid is for agents

**Negative:**
- Slower — sync mode polls a background job rather than processing inline
- Adds latency proportional to the agent research loop's poll interval

### C. CLI-side transformation with JSON output

The CLI produces a structured JSON blob that agents can consume programmatically, without writing files to disk.

**Positive:**
- No filesystem I/O
- Pipeable — easy to chain with jq or other JSON processors

**Negative:**
- No progressive disclosure — the consumer must parse the entire JSON blob
- Not navigable — no file-system-level hierarchy to inspect
- Not compatible with the artifact-pyramid spec's directory-based structure

## Decision Outcome

Chosen option A: CLI-side transformation with SSE-buffered directory output. The agent/answer commands get `--pyramid` and `--output-dir` flags. When `--pyramid` is set, the CLI runs the SSE stream for speed but buffers tokens internally. Progress messages go to stderr. On stream completion, the CLI writes the pyramid directory and prints only the absolute path to stdout.

### Pyramid Structure

```
<output-dir>/
├── 00-index.md                       # Pyramid scaffold + methodology
├── 01-summary/
│   └── findings.md                   # Key Findings -> Implications -> Recommendations
├── 02-analysis/                      # Per-angle stub files
│   ├── 00-index.md                   # Lenses chosen and why
│   └── dimension-*.md                # Stub files linking sources to L3
└── 03-dossiers/                      # Flat — naming convention, no subdirectories
    ├── 00-index.md                   # Source table: title, URL, consulted/discovered
    ├── consulted-*.md                # YAML frontmatter + full scraped content
    └── discovered-*.md               # Raw search results per query
```

### CLI Surface

```bash
# Default: streaming to stdout (unchanged)
groktocrawl agent "Research topic"

# Pyramid mode: prints absolute path to pyramid root
groktocrawl agent "Research topic" --pyramid
groktocrawl answer "Question" --pyramid

# With custom output directory
groktocrawl agent "Research topic" --pyramid --output-dir ./my-research

# --output-dir implies --pyramid
groktocrawl agent "Research topic" --output-dir ./my-research
```

### Consequences

**Positive:**
- Agent-to-agent handoff via absolute path — the calling agent navigates the pyramid by reading 00-index.md
- Navigable inspection for humans — open the directory in a file explorer
- Immutable evidence chain — L3 files are never modified after creation
- Methodology embedded in 00-index — reproducibility info travels with the output

**Negative:**
- L2 files are stubs (URL groupings), not full per-dimension synthesis — true dimensional analysis requires a server-side change or client-side LLM call
- Output directory must be cleaned up manually by the consumer
- Adds ~50ms of filesystem I/O to each pyramid write (negligible for research queries that take 5-30s)

## Links

- Relates to [ADR-0017](0017-grounded-qa-endpoint.md) — the answer endpoint that pyramid mode wraps
- Relates to [ADR-0022](0022-agent-sse-streaming.md) — the agent streaming that pyramid mode captures
- [Artifact Pyramid Specification](https://github.com/groktopus/artifact-pyramids)
