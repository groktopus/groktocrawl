# Make Request-Rate Caps Opt-In and Keep Only Capacity-Evidenced Limits

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-11-01
- Supersedes (in part): [ADR-0033](0033-search-volume-controls.md) defaults; [ADR-0051](0051-global-admission-control-and-cancellation.md) default budgets

## Context

GroktoCrawl accumulated several caps that reject or truncate work before it
reaches a resource: a per-client admission limiter (`AGENT_SEARCH_RATE_LIMIT`,
`10/60s`), a per-request search budget (`AGENT_MAX_SEARCHES_PER_REQUEST`, `5`),
global weighted admission budgets (`ADMISSION_*`, `64/32/32`), and a crawl
wall-clock cap (`CRAWL_MAX_DURATION_SECONDS`, `1800`). A single operator running
several agents concurrently against one instance reached these routinely.

Reviewing their provenance:

- **The `10/60s` limiter is a spend control, not a capacity bound.** ADR-0033
  introduced it after a spike burned 3,927 paid search queries in 48 hours
  (~82/hour). The shipped limit admits 600/hour, so it does not actually bound
  the pattern that motivated it; it only clips short bursts. It also fails open
  when Valkey is unavailable, which a genuine capacity guard cannot do.
- **The search budget truncates caller intent.** `5` is applied to the planner's
  own query list (`research/discovery.py`), silently dropping subqueries a
  caller asked for, not just limiting runaway loops.
- **The admission budgets have no measured derivation.** ADR-0051 explicitly
  deferred its browser-heavy load test and validated the numbers only with
  concurrent task stubs. The LLM and light-fetch values map to no documented
  CPU, memory, or provider limit.
- **The crawl wall-clock cap does not distinguish stuck from large.** The idle
  timeout already detects a crawl that stops making progress; a 30-minute wall
  clock additionally truncates legitimate large crawls.

By contrast, the browser tier has measurements behind it: measured Chromium RSS
is roughly 1.0–1.3 GiB per pool ([browser_pool_concurrency](../../benchmarks/browser_pool_concurrency.md)),
the scraper container is bounded by `mem_limit`/`pids_limit`, and a one-core
screen found p95 service time roughly doubling at 32 in-flight pages versus 16
([X ADR-0089](experimental/groktocrawl-x/0089-bound-browser-concurrency-per-effective-cpu.md)).

## Decision

Admission limiting and local count budgets are **opt-in**, and defaults no
longer reject or truncate work.

- `AGENT_SEARCH_RATE_LIMIT` defaults to empty ("off"). Empty, `off`, `none`,
  `disabled`, and `0` build a no-op limiter. The mechanism remains for
  deployments that pay per search.
- `AGENT_MAX_SEARCHES_PER_REQUEST` defaults to `0` (unbounded). A positive
  value still truncates the planner's query list and still raises the
  budget error; callers that want that behavior opt in.
- `ADMISSION_LIGHT_FETCH_LIMIT`, `ADMISSION_BROWSER_LIMIT`, and
  `ADMISSION_LLM_LIMIT` default to `0`. A budget of `0` means unlimited; the
  controller still counts active work so the gauge and `release()` stay
  symmetric. `deploy/scaleout.py` continues to set a browser budget from
  replica count.
- `CRAWL_MAX_DURATION_SECONDS` defaults to `0` (unbounded). The idle timeout
  (`CRAWL_IDLE_TIMEOUT_SECONDS`, default 300) remains the stall detector.
- The research-memory sweep keeps a dedicated one-per-minute guard: it is an
  admin full-index scan, not agent workload.

The following remain unchanged because they bound a physical resource, a
safety boundary, or a contract: `SCRAPER_MAX_BROWSER_CONCURRENCY`, the browser
pool process bound, `mem_limit`/`pids_limit`, per-origin politeness, crawler
page/depth limits and per-job concurrency, document/download size and SSRF
checks, cancellation, and the evidence/prompt size contracts.

`mcp-svc` follows the same principle for its own client-side bounds: the HTTP
timeout default rises to 300 seconds (a patience bound, not a service limit),
and the 429 retry ceiling rises to 60 seconds so a retry budget can span one
rejected admission window.

## Consequences

Positive:

- A single operator running several agents is limited only by measured
  physical capacity and origin politeness, not by undocumented counts.
- Every remaining rejecting cap can name the resource it protects.
- Deployments that need the old behavior can restore it with environment
  variables; no mechanism was deleted.

Negative / limitations:

- A deployment that enables a metered search provider without setting
  `AGENT_SEARCH_RATE_LIMIT` loses its burst protection. The `.env.sample`
  documents the opt-in explicitly.
- With admission disabled, an extreme fan-out can reach container memory
  limits and be OOM-killed instead of queueing. Operators who need graceful
  overload behavior should configure measured budgets.
- The admission numbers still need a real load test before any default is
  re-introduced; this record does not claim they were validated.

## Alternatives considered

- **Keep the defaults and document tuning.** Rejected: the defaults reject
  realistic single-operator work and their provenance does not support them.
- **Keep a generous fixed guard (e.g. `1000/min`).** Rejected as the default:
  it is still an unmeasured number, and any positive value is available to
  operators who want it.
- **Derive admission defaults from a new load test in this change.** Deferred:
  the load test is its own piece of work; disabling the unvalidated caps does
  not depend on it.

## Links

- [ADR-0033 Search Volume Controls](0033-search-volume-controls.md)
- [ADR-0051 Global Admission Control](0051-global-admission-control-and-cancellation.md)
- [ADR-0053 Retryable Rate-Limit Contract](0053-retryable-rate-limit-contract.md)
- [ADR-0061 Scraper Scale-Out Capacity](0061-scraper-scaleout-capacity.md)
- [Browser pool concurrency evidence](../../benchmarks/browser_pool_concurrency.md)
