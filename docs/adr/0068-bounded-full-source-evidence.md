# 0068: Select bounded evidence from complete sources

- Status: proposed
- Date: 2026-10-07

## Context

Opening-prefix context discarded late facts even though acquisition retained complete Markdown. Result slicing also concealed available upstream-page results. A target source count did not establish exhaustive discovery.

## Decision

Research synthesis, grounded answers, and session query/deepen select deterministic, verbatim lexical passages across complete acquired bodies. The default aggregate source-text budget is 32,000 Unicode characters. Answer and session callers can request 256–128,000 characters; invalid budgets are rejected. Context headers are outside this source-text budget. Selection is application owned, provider independent, and leaves full artifacts intact.

Report source identities, SHA-256 of UTF-8 content, character spans, selected/omitted counts, and text coverage. Preserve metadata on streaming completion and cached research answers. Discovery targets describe a bounded acquisition goal, never exhaustive coverage. Full bodies remain request scoped or in the existing session store with its TTL; research-memory cache retains compact metadata, not a new source archive. Agent callers can export full source content with the existing include_source_content flag.

Fast, non-streaming keyword search accepts page/offset and returns request-shaped continuation for remaining results on that upstream page. Next-page availability stays unknown. Continuations repeat the upstream query and can change order; they are not stable snapshots. Unsupported mode combinations reject continuation instead of ignoring it. Limit is a validated resource bound (1–1,000), not a statement of upstream availability.

## Consequences

Late query-matching text becomes available without unbounded model context or a vector/GPU dependency. Lexical matching does not prove entailment, semantic recall, or answer completeness, and can miss paraphrases or split a table. Low budgets spread across many sources can yield empty spans. Callers inspect metadata, increase the budget, narrow session ref_ids, resolve full refs, or acquire further pages. This change does not remove discovery attempt/deadline safeguards, guarantee engine pagination, or replace the plan/enrichment pipelines and rich-search preview contracts. Highlights, summaries, and gap analysis use whole-text lexical selection within their existing stage budgets.
