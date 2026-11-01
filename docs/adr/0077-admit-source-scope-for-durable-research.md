# ADR-0077: Admit and enforce a source scope for durable research

**Status:** accepted

**Deciders:** @magnus919

**Date:** 2026-10-31

## Context

`POST /experimental/research/v1/runs` fixed the execution boundary (ADR-0070)
but accepted only an objective. A caller could ask in prose to use one official
source and still receive an unconstrained search/capture set. The JSON request
also ignored unknown fields, so a client that sent `urls` or `allowed_domains`
received a normal `202` for an unconstrained run.

The durable path already has bounded acquisition, retained operation receipts,
source/byte budgets, and an explicit insufficient-coverage outcome for known
capture failures. What was missing was a caller-admitted way to say which
sources are eligible before any search or capture occurs.

## Decision

Accept an optional `source_scope` object on durable run creation:

- `mode`: `open` (default), `seeds_only`, or `allowlist`.
- `seed_urls`: at most eight absolute HTTP(S) URLs.
- `allowed_hosts`: at most twenty bare DNS names; exact host and subdomains
  match.

`open` preserves today's behavior. `seeds_only` acquires exactly the supplied
URLs and performs no search or model planning call. `allowlist` may search, but
search results outside the allowed hosts are discarded before acquisition, and
any seed must itself be in scope.

Unknown request fields are rejected instead of ignored. A scoped admission is
pinned to policy `durable-live/6` and persists the scope in the admitted
payload. Unscoped admissions keep the existing payload shape and idempotency
digest so retries and retained `durable-live/5` admissions replay unchanged.
`durable-live/6` includes every `durable-live/5` execution branch.

When a constrained run acquires no permitted source, it terminates with
`source_scope_unavailable`, `coverage: insufficient`, and a message stating that
no out-of-scope source was substituted. It does not publish a source-less
report; ADR-0073 requires retained source evidence.

## Consequences

- Discovery and publication now share one caller-visible boundary. A caller can
  rely on the source list instead of prose in the objective.
- Strict scopes can fail when the permitted source is unavailable or does not
  answer the question. That is an explicit result, not a fallback to wider
  sources.
- Seeds count against the existing eight-source and 256,000-byte budgets.
- The scraper still follows redirects and its response reports the requested
  URL. An allowed URL that redirects to another host is therefore captured under
  the requested identity. Effective-URL enforcement needs a scraper contract
  change and is not claimed here; the capability document records
  `redirect_effective_url_enforced: false`.
- Private-destination checks remain the scraper's fetch-time responsibility.
  Admission validates URL syntax, not network reachability.
- CLI `research create` accepts `--seed-url`, `--allowed-host`, and
  `--source-mode`; MCP `research_create` accepts the equivalent list/mode
  arguments. When the mode is omitted, seeds imply `seeds_only` and allowed
  hosts imply `allowlist`.

## Links

- [Issue #688](https://github.com/groktopus/groktocrawl/issues/688)
- [ADR-0070](0070-execute-live-durable-research.md)
- [ADR-0073](0073-publish-source-bounded-research.md)
- [Durable research guide](../guides/durable-research.md)
