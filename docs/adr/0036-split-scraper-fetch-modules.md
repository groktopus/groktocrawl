# ADR-0036: Split scraper fetch.py into Focused Modules

**Status:** proposed

**Deciders:** @magnus919

**Date:** 2026-06-16

## Context

`scraper-svc/scraper/fetch.py` is 1711 lines — the largest single file in GroktoCrawl. It mixes:

- **Cache logic** (lines 111-467): duplicates functions already present in `cache.py`
- **Content type detection** (lines 503-600): duplicates `cache.py` helpers
- **Fetch tier implementations** (lines 817-1090): llms.txt, content negotiation, Playwright
- **Playwright helpers** (lines 884-1040): proxy config, browser service integration
- **FlareSolverr fallback** (lines 1088-1145): anti-bot bypass
- **HTML-to-markdown** (lines 1147-1174): conversion pipeline
- **Quality assessment** (lines 582-770, 1344-1382): barrier classification, quality scoring, acceptability gating
- **Politeness** (lines 1423-1481): per-domain rate limiting integration
- **Orchestrator** (lines 1483-1711): `smart_scrape()` — the main entry point that wires tiers together

PR #220 attempted to split fetch.py but only extracted some helpers into sibling modules (`fetch_strategy.py`, `fetch_screenshot.py`) while leaving the core monolithic. The refactored file is still 1711 lines.

A file this large has concrete costs:
- **Code review latency:** a change to any function means a reviewer must understand 1700 lines of context
- **Merge conflicts:** multiple PRs touching the same file conflict even when modifying unrelated sections
- **Cognitive load:** new contributors must parse all 1711 lines to find the function they need

## Decision Drivers

1. **Operability:** operators should be able to find a specific fetch tier without searching 1711 lines
2. **Reviewability:** changes to one tier should not require understanding all tiers
3. **Reuse:** cache functions in fetch.py should use the canonical implementations in cache.py
4. **Stability:** the smart_scrape() public API must remain backward-compatible

## Considered Options

### Option A: Extract fetch tiers only

Move the three fetch implementations into `fetch_tiers.py` (~500 lines), leave everything else in `fetch.py` (~1200 lines).

**Pros:** minimal diff, low risk, clear delineation
**Cons:** doesn't solve the duplication with cache.py, leaves the orchestrator large

### Option B: Extract tiers + deduplicate cache → full modular split (chosen)

1. **Remove duplicated cache functions** from `fetch.py` — import from `cache.py` instead
2. **Remove duplicated content-type helpers** — import from `cache.py`
3. **Extract `fetch_tiers.py`** — `fetch_via_llms_txt()`, `fetch_via_content_negotiation()`, `fetch_via_playwright()`, `fetch_via_flaresolverr()`, Playwright proxy helpers, browser service integration (~600 lines)
4. **Extract `fetch_quality.py`** — `_add_quality()`, `_quality_acceptable()`, `_is_bot_challenge()`, `_classify_barrier()`, `_has_embedded_content()`, `_looks_like_markdown()`, `html_to_markdown()` (~350 lines)
5. **Keep `fetch.py`** — `smart_scrape()` orchestrator + politeness integration + module-level config (~350 lines)

**Pros:** achieves target of each module <500 lines, eliminates cache duplication, clear separation of concerns
**Cons:** larger diff, requires updating all imports in tests and callers

### Option C: Monolithic + cleanup only

Remove duplication with cache.py, add section dividers, leave as one file.

**Pros:** zero structural risk
**Cons:** does not address the reviewability or merge-conflict drivers

## Decision

**Option B** — Extract tiers + deduplicate cache into a full modular split.

## Consequences

### Positive

- Each module <500 lines, reviewable in isolation
- Cache deduplication removes ~250 lines from fetch.py
- A new fetch tier (e.g., API-first extraction) only touches `fetch_tiers.py`
- Quality heuristic changes only touch `fetch_quality.py`
- `smart_scrape()` in `fetch.py` becomes a readable ~150-line orchestrator

### Negative

- Import path changes require updating test mocks
- Cross-module function calls add one level of indirection
- Multiple PRs touching different modules simultaneously may create import-order conflicts

### Neutral

- The public API (`smart_scrape(url) -> dict`) is unchanged
- All existing tests pass without modification once imports are updated

## Module Boundaries

```
scraper-svc/scraper/
  fetch.py          (~350 lines) — smart_scrape() orchestrator + config + politeness
  fetch_tiers.py    (~600 lines) — the three tier implementations + Playwright helpers
  fetch_quality.py  (~350 lines) — content quality assessment + barrier detection
  cache.py          (existing, ~470 lines) — canonical cache implementation
  fetch_strategy.py (existing, ~923 lines) — unchanged
  fetch_screenshot.py (existing, ~300 lines) — unchanged
```

## Links

- PR #220: prior fetch.py split attempt
- Issue #240: split fetch.py orchestrator into focused modules
- cache.py: canonical cache module that fetch.py currently duplicates
