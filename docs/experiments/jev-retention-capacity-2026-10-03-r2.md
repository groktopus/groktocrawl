# GroktoCrawl direct-scraper capacity continuation (R2)

**Study ID:** GCX-CAP-036-R2. This is a prospectively registered continuation of
GCX-CAP-036-R1 over the same frozen 34-URL pool. It is distinct from upstream
SlopSearX EXP-036. The pool is the private blind ordering of SlopSearX EXP-036
q1; its public card mapping and digest are recorded in the [R1 transport
protocol](jev-retention-capacity-2026-10-03-transport-addendum.md).

## Reason for this addendum

R1 completed the canary and 21 additional width-1 requests before stopping at
the first non-200 response. It recorded 21 HTTP 200 responses and one HTTP 502
for public card `c1` (pool rank 22, an arXiv PDF result). The exact frozen URL
was `https://arxiv.org/pdf/2601.13295`. The response was classified as
`http_error`, tier `unknown`, with no content. No retry was made. The service
remained healthy in a separate read-only postflight; this does not prove that
the page caused the 502 or establish general service health during every
request. See the private R1 receipt ledger and public aggregate outcome for the
complete retained evidence.

The first run's stop rule treated every non-200 as a reason to stop the whole
sweep. This R2 amendment registers the remaining fixed-pool capacity slice
before any further URL requests. A returned HTTP status is retained as that
URL's outcome; status 429 remains an immediate stop. Transport errors,
unhealthy preflight, candidate revision change, or an unrelated active
research job also stop new admissions. There are no automatic retries or
replacement URLs. Card `c1` appears again only because R2 replays the full
frozen pool, not as a targeted retry.

## Frozen request plan

Use the exact pool SHA-256
`9c903f589ef1c247aa7364b1769013032c1fb53088ebd98bd837d6d05ea9004d` and its
34 URLs without exclusions. The 170 new starts are five fixed passes, in this
exact order: width 1/repeat 1, width 3/repeat 1, width 5/repeat 1, width
3/repeat 2, and width 5/repeat 2.
This yields 34 width-1 requests and 68 each at widths 3 and 5. The first pool
URL is the first request of the width-1 pass, not an extra request. Together
with the 22 R1 starts, this stays below the original 204-start bound (192 starts maximum across R1
and R2). Each start is fsynced to a new append-only, mode-0600 journal before
dispatch; each response is recorded separately. Counts derive from start
events. No retry, cache clearing, search, Jev, LLM, job creation, indexing,
configuration change, deployment, or runtime modification is in scope.

Pin the candidate revision to
`68ece0cfb50684a5637185048d0db15049bb9d56`, the revision observed in R1, and
use its runtime-configured internal scraper route. Before starting and at each
sweep boundary, capture the reviewed read-only health, route, tier/call metrics, active jobs, revision, and aggregate
resource snapshot. Stop if health/metrics are unavailable, revision changes,
an unrelated job is active, transport fails, or status 429 is returned. Other
HTTP statuses remain page-level results and do not trigger a retry; record the
status, failure category, tier, and elapsed time without body text. Cancel
queued work after a stop and allow already-started requests to drain for up to
90 seconds. Admit requests for at most 30 minutes, with one request per origin
at a time and a 70-second per-request timeout. A separate read-only postflight
with a 60-second timeout runs in a `finally` path for success, stop, and
exception outcomes. Keep any service-level counter movement unattributed when
other traffic may have occurred.

This remains a direct scraper-service slice, not an end-to-end agent queue
capacity or lifecycle test. It does not test progress reporting, cancellation
of agent jobs, concurrent jobs, slow/failing mixed tiers at 25–40 sources,
answer synthesis, chunk complementarity, or contradiction handling. Those
criteria remain unmeasured. Report the resulting outcome as a bounded capacity
observation, not a production-readiness claim.


## Execution status

R2 completed the registered 170 starts with no stop condition. The aggregate results, per-pass latency and tier counts, R1/R2 distinction, and
unmeasured criteria are recorded in the
[capacity outcome](evidence/jev-retention-2026-10-03/exp036-capacity-outcome.md).
This status note does not change the frozen request plan.
