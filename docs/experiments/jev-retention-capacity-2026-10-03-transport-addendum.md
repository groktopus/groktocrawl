# GroktoCrawl direct-scraper capacity run: corrected transport preregistration

**Study ID:** GCX-CAP-036-R1. The earlier private working label `EXP-036`
refers to this GroktoCrawl scraper-capacity work only; it is distinct from
SlopSearX EXP-036 (held-out reranking). This run reuses the fixed 34-URL
q1-returned pool captured by SlopSearX EXP-036; it does not repeat that search.
The source card records are public in
[SlopSearX EXP-036 q1-frozen.json](https://github.com/magnus919/SlopSearX/blob/main/docs/experiments/evidence/EXP-036/q1-frozen.json)
(file SHA-256 `f61501a1548affe232a7bd47e26a6e2427117d9b12171f5b2c7af1669b571e56`).
The acquisition runner's opaque IDs and order are blind-shuffled; exact URL
matching establishes this complete mapping from private pool order to public
card IDs:

| Pool rank | Public card ID | Pool rank | Public card ID |
|---:|:---|---:|:---|
| 1 | c6 | 18 | c24 |
| 2 | c5 | 19 | c12 |
| 3 | c14 | 20 | c31 |
| 4 | c23 | 21 | c13 |
| 5 | c18 | 22 | c1 |
| 6 | c27 | 23 | c11 |
| 7 | c28 | 24 | c20 |
| 8 | c2 | 25 | c25 |
| 9 | c26 | 26 | c17 |
| 10 | c4 | 27 | c33 |
| 11 | c0 | 28 | c15 |
| 12 | c21 | 29 | c22 |
| 13 | c32 | 30 | c10 |
| 14 | c7 | 31 | c8 |
| 15 | c30 | 32 | c19 |
| 16 | c29 | 33 | c16 |
| 17 | c3 | 34 | c9 |

**Status:** pending independent protocol review; no new public URL requests have
been made under this addendum. The original GCX capacity receipts remain a
separate, zero-credit setup/transport outcome.

## Reconciliation of the first attempt

The original private ledger contains 102 receipt objects for 34 candidate IDs:
34 each at caller widths 1, 3, and 5; all are `URLError`, have no HTTP status,
and have no returned tier. The receipt-array SHA-256 is
`c31dd126ee5fb82e64f8baecdf4ec1200f207b34e915af93e9604ee78ddfff52`. After
the attempt, I manually set the ledger's `requests_completed` counter to this
count, without preserving the previous value. This is a provenance deviation.
This addendum independently derives the count from the receipt objects; the
existing ledger and receipts will not be rewritten further.

Read-only diagnostics inside the candidate agent used the same
`load_settings().scraper_url` base intended for `/scrape`. Health and OpenAPI
were reachable, and OpenAPI exposed `GET /health` and `POST /scrape`. The
configured transport target differed from the literal used by the first-run
helper. The configured host resolved; the helper's literal host produced DNS
`EAI_AGAIN` (errno -3) in the candidate agent. The historical receipts retain
only `URLError`, so this repeat diagnostic identifies the likely cause but
cannot retrofit per-request errno values. The scraper's current cumulative
counters are success=2059 and error=229; the original preflight did not capture
a `scrape_calls_total` baseline, so no delta is inferable. No direct origin-
request log is in the retained evidence. Since the helper failed while opening
its internal service transport, these attempts receive no source or scraper-
quality credit; exact per-attempt origin activity remains unverified.

## Corrected protocol

Use the exact privately retained SlopSearX EXP-036 q1 pool, SHA-256
`9c903f589ef1c247aa7364b1769013032c1fb53088ebd98bd837d6d05ea9004d`. Do not
search for replacement URLs. The corrected helper must derive the internal
`/scrape` URL from the candidate agent's runtime `load_settings().scraper_url`;
it must not contain a literal service hostname. It may return only sanitized
status, failure/reason category, source tier, warning/degraded state, Markdown
length, elapsed time, and content digest. Never log or print the configured
base, credentials, URLs, titles, body text, or raw responses.

Create a new private run ledger and append-only attempt journal; do not append
to or replace the first ledger. Before dispatching every request, fsync a
sanitized start event containing candidate ID, URL digest, sweep, and canary
flag. Append a separate result event after response. Derive request budget and
started/in-flight counts only from start events; never hand-edit the count. If
interrupted, an unmatched start is an attempted request with no recorded
result, not a source-quality failure.
Start with a single canary: the first URL in frozen pool order, at caller width
1. Capture a private per-URL receipt. If the configured internal transport
fails before an HTTP response, record the exception class and sanitized reason
category and stop immediately. Do not retry the canary or issue other URLs. A
canary HTTP 429 or any status other than 200 is an immediate stop and must not
enter the remaining sweep. An HTTP 200 demonstrates the corrected internal
route is reachable; retain a scraper-level unsuccessful/empty outcome and
continue only if health, revision, active-job, metrics, and resource checks
remain within the parent-reviewed baseline.

If the canary passes, continue the fixed pool at caller widths 1, 3, and 5,
then repeat each width once in that order. Count the canary as the first URL of
width 1/repeat 1. Maximum: 204 internal scraper request starts including the
canary, one request per origin at a time, 70 seconds per request, a 30-minute
request-admission window, and no retries. Do not call the repeat a warm-cache
pass unless cache state is directly observable. Stop admitting requests when
the 30-minute window closes or the 204-start bound is reached. Already-started
requests may drain for up to 90 seconds; the final read-only postflight has a
separate 60-second timeout. Stop earlier on any unexpected internal transport
failure or non-200 internal response, failed required preflight, candidate
revision change, unrelated active research job, or HTTP 429. Preserve every
attempt-start and result event and the stop reason.

Immediately before the canary and at each sweep boundary, read health,
OpenAPI route presence, parsed scraper tier counters, parsed
`scrape_calls_total`, active research job count, candidate revision, and
aggregate CPU/memory through the existing read-only path. Capture the same
after the run. These are service-level observations, not proof of general
capacity or proof that unrelated traffic did not occur. Aggregate counter
movement can include concurrent service activity;
attribute only request-level outcomes to this probe's own journal. No
`/v2/scrape`, search, Jev, LLM, job creation, indexing, cache clearing,
configuration change, deployment, or runtime modification is in scope.

This run measures only direct scraper-service behavior over this returned
pool and candidate revision. It does not measure end-to-end agent scheduling,
progress, cancellation, two-job concurrency, answer uplift, or production
reliability. Those criteria remain unmeasured and cannot be inferred from this
bounded run.
