# Retire the Per-Query Search Maximum and Acquire Every Distinct Result

- Status: accepted and implemented in the experimental fork
- Decider: Magnus Hedemark
- Date: 2026-09-21
- Scope: experimental agent research acquisition in `magnus919/groktocrawl-x`; not a mainline deployment-default change
- Supersedes: [ADR-0091](0091-bound-research-acquisition-by-search-maximum.md)
- Related: [ADR-0065](../../0065-stream-discovery-acquisitions.md), [ADR-0082](0082-delegate-bounded-retrieval-to-slopsearx.md), [ADR-0089](0089-bound-browser-concurrency-per-effective-cpu.md), [ADR-0090](0090-optional-post-scrape-jev-noise-filter.md)

## Context and Problem Statement

ADR-0091 replaced the historical source-acquisition quotas with a local
per-query search maximum. That still makes the agent discard eligible search
results before acquisition: the agent applies a result cap after SlopSearX
has returned its response, even though the upstream response is already the
provider's selected result set.

The user-facing decision is now to retire that agent-only maximum. For each
research query, the agent should acquire every distinct URL that SlopSearX
actually returns. Distinctness is URL-level deduplication within the research
acquisition policy, not a guarantee that every URL will scrape successfully or
survive later quality and evidence filters.

SlopSearX's current `/search` API does not accept a maximum-result parameter.
This decision therefore does not add one, and it does not require the
upstream service to return an unbounded result set. It removes the
GroktoCrawl agent's extra local result cap.

## Decision Drivers

- Preserve every distinct URL the delegated search service actually returns
  for agent research.
- Remove an implicit source-selection quota that is invisible to the search
  provider and caller.
- Keep URL deduplication, acquisition failures, evidence-quality filters, and
  operational capacity controls explicit and inspectable.
- Preserve the existing contracts of `/v2/search` and other non-agent
  endpoints, and keep explicit caller-selected `max_credits` meaningful.
- Keep the experimental fork bounded, observable, cancellable, and reversible.

## Considered Options

| Option | Benefit | Cost or reason not selected |
|---|---|---|
| Keep the local per-query search maximum from ADR-0091 | Predictable local source count | Silently discards URLs SlopSearX selected and adds a second selection policy |
| Remove the agent cap and acquire every distinct returned URL | Faithfully delegates result selection to SlopSearX; simplest evidence boundary | May increase scrape work and requires runtime capacity controls to remain enforced |
| Add a result maximum to SlopSearX | Could centralize truncation | The current `/search` contract has no such parameter and changing it is outside this decision |

## Decision

For experimental agent research, the acquisition path will not apply an
agent-only per-query result maximum. After each SlopSearX response, it will
normalize URLs and admit every distinct URL actually present in that response
to the scrape-attempt set. Across multiple research queries, the same URL is
acquired at most once under the existing research deduplication policy.

This is an admission rule, not a success guarantee. A scrape can fail, be
blocked, be rejected by a later content or evidence-quality filter, or be
cancelled by an operational guard. Those outcomes remain observable and do
not justify silently dropping another distinct URL before it is attempted.

Bounded concurrency, per-origin pacing, timeouts, cancellation, process and
memory limits, downstream admission control, and other operational guards
remain mandatory. They bound execution and protect the system; they are not a
replacement result quota.

The upstream SlopSearX `/search` contract remains unchanged. No new
maximum-result parameter is sent. The non-agent `POST /v2/search` endpoint
retains its existing caller-provided `limit` behavior, as do rich search,
`/v2/answer`, crawl, map, scrape, webhooks, and other non-agent endpoints.
This ADR changes only experimental agent acquisition.

The agent API contract retires the `max_results_per_query` request field. It
is removed from `AgentRequest`; sending that old JSON field explicitly is
rejected with HTTP 422 rather than silently ignored. The corresponding CLI
flag and MCP argument are removed as well. Existing callers should omit the
field/flag/argument; `max_credits` remains the explicit attempt-budget
control.

An explicitly supplied `max_credits` remains a caller-selected tighter
attempt budget for the agent API. It is not the retired result maximum: when
present, it may prevent every returned URL from being attempted, by explicit
caller choice. When omitted, no independent result or scrape-count cap is
applied by the default agent acquisition policy.

## Confirmation and Evidence

- The experimental implementation removes the agent-only result slicing and
  retains URL normalization and deduplication before scrape admission in
  `agent-svc/agent/research/discovery.py` and the research loop callers.
- Focused regression coverage in
  `tests/service/test_research_scrape_all.py` and
  `tests/service/test_agent_search_result_limit.py` shows that all distinct
  URLs returned by a SlopSearX response are admitted, duplicates are attempted
  once, failures do not stop later eligible URLs, and explicit `max_credits`
  still narrows attempts when requested.
- Contract checks must show that `/v2/search` still honors its own `limit` and
  that no maximum-result parameter is sent to SlopSearX.
- API, CLI, and MCP contract checks must show that `max_results_per_query` is
  absent and that an explicit legacy API field receives HTTP 422. The focused
  API/client coverage is in `tests/service/test_agent_search_result_limit.py`.
- CI and release-gate outcomes are intentionally not recorded here; they are
  evidence for the containing change, not part of the decision record.

## Consequences

- Agent research preserves more of the search provider's selected evidence
  and has a simpler source-admission explanation.
- Requests may attempt more URLs and consume more scrape capacity. Existing
  concurrency, pacing, timeout, cancellation, and admission controls must be
  monitored and remain effective.
- Explicit `max_credits` continues to provide a caller-controlled escape hatch
  for narrower attempt budgets without reintroducing a hidden default cap.
- ADR-0091 remains immutable historical rationale except for its successor
  status/link; this record governs the replacement decision in the
  experimental fork only.
