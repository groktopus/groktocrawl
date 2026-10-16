# Retain Bounded Source Media Metadata

* Status: proposed; implementation prepared for review
* Date: 2026-10-07
* Issue: #421

## Context

SlopSearX already reports structured image/video records (`MediaInfo` in
`slopsearx/adapter.py`, JSON serialization in `slopsearx/formatter.py`, and
fixtures in `tests/test_media_contract.py`). The fork's shared scholarly
metadata allowlist dropped these records. Some streaming and session paths
also dropped engine attribution.

## Decision

Extend ADR-0093's additive source metadata projection with optional `media`:
`media_type` (`image` or `video`), `url` (reported original), `thumbnail`,
`source` (reported page), `width`, `height`, and `duration` (seconds).
Normalize reported SearXNG category/URL aliases only when a known image/video
category or media kind is present. Unknown fields, embeds and arbitrary HTML
are excluded. Missing values remain absent. News retains its existing publication
metadata; no image or video kind is inferred from a news result.

Use the same projection at acquisition, API/SSE, citation metadata and session
reference boundaries. CLI JSON and MCP preserve the API record without another
media schema or network request. Existing image-result fields remain compatible;
reported original URL/dimensions take precedence over legacy resolution text.

URLs are limited to 2,048 characters and HTTP(S), without percent-escaped authority, userinfo, control
characters, known private/local hosts or credential/signature query parameters.
The policy does not resolve DNS and therefore cannot establish whether a public
hostname later resolves privately; these values are discovery metadata, never
permission to fetch/embed a remote resource. Consumers must apply their own
fetch/render policy. Dimensions are integer pixels 1–100,000; duration is finite
seconds 0–604,800. At most three URLs are retained. No upstream keys are copied
wholesale, no dimension/duration is fabricated, and no media body enters evidence.

## Consequences

Old results omit the additive fields. Scholarly metadata, URL identity and
cross-engine attribution are preserved. Session retention/deletion uses existing
reference lifetimes, with no separate media store. Unsafe URLs are omitted rather
than redacted into a potentially broken link. This change neither transcribes
video nor introduces an image UI or external media retrieval.

## Links

* [Source provenance](0093-retain-source-search-provenance.md) — extended
* [Issue #421](https://github.com/magnus919/groktocrawl-x/issues/421)
