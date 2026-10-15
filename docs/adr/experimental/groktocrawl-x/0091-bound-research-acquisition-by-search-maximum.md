# Bound Research Acquisition by the Per-Query Search Maximum

- Status: superseded by ADR-0092
- Decider: Magnus Hedemark
- Date: 2026-09-21
- Scope: experimental agent research acquisition in `magnus919/groktocrawl-x`; not a mainline deployment-default change
- Related: [ADR-0065](../../0065-stream-discovery-acquisitions.md), [ADR-0082](0082-delegate-bounded-retrieval-to-slopsearx.md), [ADR-0089](0089-bound-browser-concurrency-per-effective-cpu.md), [ADR-0090](0090-optional-post-scrape-jev-noise-filter.md)
- Successor: [ADR-0092](0092-retire-per-query-search-maximum-and-acquire-all-distinct-results.md)

## Context and Problem Statement

ADR-0065 bounded discovery with several independent source-acquisition
stopping rules: a three-success target, at most twenty novel attempts, and a
twenty-result/rank-and-credit admission policy. Those rules make source selection a
second quota layered on top of search. They can stop before every result that
the requested search maximum made available has had a chance to contribute to
the answer.

The experimental research policy needs a simpler and more inspectable contract:
the per-query search maximum is the default source-count bound. Every
deduplicated SlopSearX result within that bound receives a scrape attempt. A scrape attempt
may fail, be blocked, be rejected by a later quality filter, or be cancelled
by an operational guard; the decision is about admission to acquisition, not
guaranteed successful sources.

SlopSearX's current `/search` API does not accept a maximum-result parameter.
GroktoCrawl therefore applies the requested maximum locally after receiving
the response. This ADR does not change SlopSearX or imply that its API gains a
new parameter.

## Decision Drivers

- Make the source-admission rule correspond to the caller's requested search
  maximum.
- Avoid silently discarding eligible search evidence because an unrelated
  success or attempt quota was reached first.
- Keep resource protection explicit and separate from source selection.
- Preserve the existing contracts of non-research endpoints and inherited
  mainline behavior.
- Keep the experimental fork bounded, observable, cancellable, and reversible.

## Considered Options

| Option | Benefit | Cost or reason not selected |
|---|---|---|
| Keep the three-success, twenty-attempt, and rank/credit admission rules | Familiar historical behavior | Adds hidden source quotas and can omit eligible results before acquisition |
| Use the per-query search maximum as the default source-count bound | Simple caller-visible contract; every eligible deduplicated result gets an attempt | May perform more failed or low-value scrapes; requires separate resource controls |
| Pass a result maximum to SlopSearX | Could move truncation upstream | The current `/search` API does not accept that parameter; it would be an incompatible API assumption |

## Decision

For experimental agent research, after each query result set is received,
normalize and deduplicate its results, then admit every result within the
requested per-query search maximum to the scrape-attempt set. Do not stop that
admission because three sources have succeeded, because twenty attempts have
been made, or because a twenty-result/rank-and-credit threshold has
been reached. The requested
search maximum is the only source-count bound in the default policy. An
explicit caller-selected `max_credits` remains a tighter attempt budget for
API compatibility, as described below.

Bounded concurrency, per-origin pacing, timeouts, cancellation, process and
memory limits, downstream admission control, and other operational guards
remain mandatory. They control how much work can run at once or how long it
may run; they are resource controls, not source quotas, and do not authorize
silently dropping an eligible result merely because capacity is temporarily
occupied. A result can still have no successful artifact when its attempt
fails or an operational guard terminates the request.

The current SlopSearX `/search` contract remains unchanged. The local
GroktoCrawl client continues to apply its per-query maximum to the returned
results; no new SlopSearX request parameter is assumed.

The default agent research policy has no independent scrape-count cap beyond
the requested per-query search maximum. When a caller explicitly supplies
`max_credits`, the experimental agent honors it as a tighter attempt budget;
this preserves the existing API contract and makes the narrower caller
constraint intentional rather than an implicit discovery rule. The explicit
caller constraint takes precedence for that request, while it does not change
the default policy for callers that omit the field. This ADR does not remove,
deprecate, or reinterpret the request field in other surfaces.

This decision applies only to the experimental agent research acquisition
path. `/v2/search`, rich search, `/v2/answer`, crawl, map, scrape, webhooks,
and other non-agent endpoints retain their existing contracts unless a
separate ADR explicitly changes them. It does not change mainline
GroktoCrawl.

## Confirmation and Evidence

- The experimental acquisition path now deduplicates the eligible result set,
  admits every eligible URL, and disables the historical three-success and
  twenty-result stopping rules while retaining bounded concurrency.
- Focused regression coverage in
  `tests/service/test_research_scrape_all.py` exercises exhaustive result
  acquisition, duplicate removal, failed attempts, explicit credit budgets,
  video URLs, and the five-task concurrency ceiling.
- The existing SlopSearX client contract remains local-truncation based: no
  new maximum-result parameter is sent to `/search`.
- Explicit `max_credits` remains a compatibility override and can reduce the
  number of attempts for a request; callers that omit it receive exhaustive
  acquisition within the search maximum.
- Reassess this override after the experimental fork has representative
  request-volume, failure-rate, latency, and evidence-coverage data. Any
  future removal or semantic change requires a new ADR or an explicit
  superseding decision with API compatibility review.

## Consequences

- Research can attempt more sources than the historical three-success or
  twenty-attempt rules would have allowed, improving the chance of retaining
  relevant evidence from the requested search set.
- Scrape failures and low-value pages may consume more operational capacity;
  bounded concurrency and other runtime guards must therefore remain
  enforced and observable.
- The source set is easier to explain: search maximum bounds admission, while
  concurrency and timeout policies bound execution.
- Existing endpoint behavior and the inherited mainline decision remain
  unchanged. In this experimental fork, this record supersedes ADR-0065's
  source-acquisition stopping policy; ADR-0065 remains immutable history.
