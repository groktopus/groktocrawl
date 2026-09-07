# Extend Source Artifact Reuse Across Research Passes

- Status: accepted
- Deciders: GroktoCrawl maintainers
- Date: 2026-09-04

## Context

The canonical deep research loop can run a gap-focused discovery pass after
its initial pass. Before this decision, each discovery invocation tracked its
own URLs and scraped Markdown, so a follow-up search could fetch the same page
again, append duplicate context, and report duplicate sources. A warm scraper
cache could reduce network work but did not remove duplicate extraction,
context, or source accounting.

## Decision Drivers

- Avoid duplicate fetch and extraction work within one request.
- Keep source identity conservative because query parameters and path case can
  identify different resources.
- Preserve retry behavior after failed or barrier-refused acquisition.
- Make credit use reflect novel successful acquisitions while allowing reuse.
- Keep extraction-option changes observable and compatible with streaming and
  structured-output callers.

## Decision

Discovery accepts an optional request-scoped `SourceRegistry`. The registry
maps a normalized URL identity to the latest successful, nonempty
`SourceArtifact`. Normalization lowercases scheme and host, removes default
ports and fragments, and maps an empty path to `/`; it preserves path case,
trailing slashes, and the complete query string. A registry hit is reusable only when its exact
fetch/contents option fingerprint matches the requested extraction contract.

Failed or refused acquisitions are never registered. A later pass can retry
them. New successful artifacts consume one credit each; compatible registry
hits consume none. Discovery returns unique registry-wide artifacts and
explicit novel/reused accounting so the loop can build one context and source
list. The loop compares the resulting context/evidence before deciding whether
another synthesis call is necessary.

The registry lifetime is one request. It is not a replacement for the
cross-session research-memory or scraper-cache freshness policies.

## Consequences

- Positive: duplicate-only follow-up passes perform no compatible scrape and
  retain stable unique context and source details.
- Positive: partial overlap acquires only novel compatible candidates, and
  option changes trigger an observable fresh acquisition.
- Positive: failure remains retryable and metrics expose registry reuse and
  novel sources by pass.
- Negative: the canonical loop must carry the registry and use its unique
  context/accounting when merging passes.

## Links

- [ADR-0050](0050-source-artifact-and-lightweight-only-scrape.md)
- Issue #624 — Cross-pass source artifact reuse
