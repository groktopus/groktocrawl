# Bounded acquisition capacity probe (2026-10-03)

**Study:** EXP-036. **Status:** preregistered; coordinator approved the direct
scraper-service phase after review. This is descriptive acquisition evidence,
not a filter, quality, or production-adoption experiment. It does not change
configuration, deploy a service, or modify the runtime.

## Frozen input and scope

Use the 34 distinct URLs in the privately retained EXP-036 `q1` candidate pool.
Its custodian verifies the exact pool by SHA-256
`9c903f589ef1c247aa7364b1769013032c1fb53088ebd98bd837d6d05ea9004d`; the
URLs, titles, snippets, and full page text remain outside this repository. Do
not issue a replacement search. This is the exact returned candidate pool,
not proof of complete engine coverage. Attempt each URL once per sweep. Do not
retry failed, blocked, empty, or timed-out sources.

Call the existing internal scraper service `/scrape` from inside the candidate
agent container. Do not use the public `/v2/scrape` API wrapper: its successful
responses asynchronously invoke semantic indexing, which would violate the
read-only scope. The private proxy must target this fixed candidate deployment
and return sanitized outcome, source tier, warning/degraded state, Markdown
length, and elapsed-time metadata only. Do not run search, Jev, or an LLM in
the direct phase. Do not change deployment, environment, feature flags, cache,
service state, or index contents.

## Workload and bounds

Submit the identical 34 URLs in frozen order at caller concurrency 1, 3, and 5,
then repeat each sweep once. Allow at most one request per origin at a time;
use no retries and cap each request at 70 seconds. The maximum is 204 scraper
requests. The repeat is not called a warm-cache run unless an observable cache
state is returned; do not clear any cache. No answer-quality rubric is applied.

The optional agent lifecycle probe is deferred and requires its own reviewed
preregistration. Before any such run, inspect deployed source/configuration
read-only to verify the effective Jev filter/key state, actual model-call
count, `search_type="focused"`, `strict_constrain_to_urls=true`, fixed
nonempty URLs, and `max_credits`. The default deep strategy can schedule
follow-up work, so do not use it. If TypeSafe is configured, either preregister
bounded Jev calls or omit this lifecycle probe. If a probe is later approved,
select only from successful nonempty scrape results: choose the shortest
Markdown page at or below 4,000 characters, breaking ties by frozen pool order;
if none qualifies, skip. Never count a failed or empty page as a safe zero-token
input. The 34-page corpus must not enter model context. Any such run must use
alias `free`, document its input bound, and remain at no more than two model
calls per job only after source confirms that bound. Job creation, indexing,
research-memory writes, progress/status, and cancellation side effects must be
explicitly assessed before the probe; skip it if the read-only constraint
cannot be maintained.

## Baseline, receipts, and stop rules

Before the first request, verify candidate agent and scraper health are `ok`,
confirm zero unrelated active research jobs, and prove that the coordinator-
reviewed proxy can observe aggregate health/resources and scraper tier metrics
without writing service or index state. Record the candidate revision privately.
If any baseline condition cannot be observed, do not start.

Keep complete per-URL attempt receipts in a private, access-restricted ledger:
candidate ID, attempt number, status/failure class, tier, warning/degraded
state, Markdown character count, elapsed time, and content digest. Do not store
or print credentials. Do not include URLs, titles, page bodies, raw responses,
or deployment identifiers in the committed report. Preserve failed attempts;
they are not irrelevant-source labels. Publish only sanitized aggregate counts
and descriptive statistics. Record health, aggregate CPU/memory, and tier
metrics before, during, and after the sweeps.

Stop issuing requests if health fails twice, the candidate revision changes,
an unrelated job appears, a rate-limit response occurs, or the 30-minute/204-
request limit is reached. Preserve the observed failure and stop reason; do not
retry to fill missing observations. Keep per-sweep completion/failure/refusal/
degraded counts, source-tier mix, latency summaries, observed concurrency, and
health/resource deltas separate. Treat cache warmth as unknown absent explicit
cache evidence.

This probe can describe scraper-service behavior on one 34-URL returned pool
and one candidate revision. It cannot establish general capacity, guarantee
all-result success, test 34-source agent scheduling, exercise job cancellation
or progress, or demonstrate answer uplift. Those remain unmeasured until
separately preregistered and safely exercised.
