# GroktoCrawl direct-scraper capacity outcome (GCX-CAP-036; 2026-10-03)

This GroktoCrawl study uses a 34-URL pool captured by SlopSearX EXP-036. The
study IDs refer to separate services and experiments. See the public source
card mapping in the [GCX-CAP-036-R1 transport addendum](../../jev-retention-capacity-2026-10-03-transport-addendum.md).

## Historical first attempt

The initial private ledger contains 102 attempt receipts for 34 candidates at
caller widths 1, 3, and 5. All returned `URLError`; none returned an HTTP
response, tier, or status. The helper used a literal internal-service host
instead of the candidate runtime `settings.scraper_url`; read-only diagnostics
showed the configured target resolving while the helper's literal host returned
DNS `EAI_AGAIN` (errno -3). The old receipts retained only `URLError`, so the
later diagnosis cannot recover per-request errno. The receipt array digest is
`c31dd126ee5fb82e64f8baecdf4ec1200f207b34e915af93e9604ee78ddfff52`. After the
attempt, the `requests_completed` ledger field was manually set to 102 without
preserving its earlier value. This provenance deviation is recorded, and the
historical artifacts were not rewritten further. These 102 attempts receive
zero source-quality credit.

## Corrected partial run R1

A new corrected run used the fixed pool and runtime-configured internal
`/scrape` route. Its canary returned HTTP 200. The first width-1 pass then
returned 20 more HTTP 200 responses before the next URL, public card `c1`
(pool rank 22, an arXiv PDF result), returned HTTP 502 (`http_error`, tier
`unknown`, no Markdown). R1 stopped immediately as preregistered: 22 starts and
22 result records total, 21 HTTP 200 and one HTTP 502. There was no retry or
replacement URL. Observed latency for the 21 HTTP 200 responses ranged from
86.7 to 7,259.9 ms (median 870.6 ms); this partial, rank-ordered prefix is not a
throughput or general-capacity estimate. The remaining pool and all repeat
widths were not attempted in R1.

The runner's early-stop path left its postflight field empty. A separate,
read-only postflight was therefore captured immediately afterward and retained
as a distinct mode-0600 artifact. Health was `ok`, there were zero active
research jobs, the candidate revision was unchanged, and tier metrics/resources
were available. Cumulative counters moved from success=2,062/error=229 at the
R1 baseline to success=2,083/error=230 after the read-only postflight. This
service-level movement is not attributable to this probe because other service
traffic may have occurred. The separate postflight capture is a documented
procedure deviation; it does not repair the missing field in the original
ledger.

Private artifact digests: append-only request receipts
`0f615bfa09f0c526d1940e8d34a4dac02c18fabd67b9d5ba26a2d10986219f7d`; R1
ledger `6f3039c75fd27afe21a8ea26c24b6a587fc9b15396b34fd08c8eab89edeba12c`;
separate postflight `2c58b476d97d22d2810185ead6f33f36c9a6dee4a43309a057ed0b1808f3e9d4`.
The private files contain sanitized request metadata and no complete page
text. The R1 direct-scraper experiment is incomplete and gets no broad capacity
claim.

## Registered continuation

[GCX-CAP-036-R2](../../jev-retention-capacity-2026-10-03-r2.md) prospectively
registered 170 starts over the same unfiltered frozen pool: width 1 once, width
3 twice, and width 5 twice. The R2 protocol treats HTTP statuses as per-page
outcomes, except HTTP 429 stops admission; transport, health, revision, and
unrelated-job stop rules remain.

R2 completed all five planned passes. It recorded 170 start and 170 result
events:
165 HTTP 200 and five HTTP 502. In each pass, 33 results were HTTP 200 and the
same frozen card `c1` (rank 22, the arXiv PDF) returned HTTP 502 with no content.
This was a fixed-pool replay, not a targeted retry, and the repeated failure does
not establish the cause. No 429, internal transport, health, revision, or active-
job stop condition occurred. The final postflight ran in the `finally` path:
agent and scraper health were `ok`, there were zero active jobs, and the candidate
revision was unchanged.

Per-pass per-request scraper latency summaries for successful HTTP 200 responses
are below; they are not whole-sweep completion times. The p95 uses nearest-rank.
Whole-sweep wall-clock durations were not retained. Tier counts include the one failed HTTP 502, whose
tier is `unknown`.

| Width / repeat | Results | HTTP 200 / 502 | p50 / p95 (ms) | Tier counts |
|---|---:|---:|---:|---|
| 1 / 1 | 34 | 33 / 1 | 100.8 / 7,052.1 | content-negotiation 22, Playwright 10, llms.txt 1, unknown 1 |
| 3 / 1 | 34 | 33 / 1 | 17.4 / 313.4 | content-negotiation 22, Playwright 10, llms.txt 1, unknown 1 |
| 5 / 1 | 34 | 33 / 1 | 16.4 / 390.4 | content-negotiation 22, Playwright 10, llms.txt 1, unknown 1 |
| 3 / 2 | 34 | 33 / 1 | 15.5 / 373.3 | content-negotiation 22, Playwright 10, llms.txt 1, unknown 1 |
| 5 / 2 | 34 | 33 / 1 | 16.4 / 418.7 | content-negotiation 22, Playwright 10, llms.txt 1, unknown 1 |

The same URLs and ordering were used repeatedly. Cache state was not observable,
so these passes cannot be classified as cold or warm. Do not use these latency
summaries to select a width or claim fresh-source throughput. For R2, cumulative
service counters moved from success=2,086/error=230 at baseline to success=2,251/
error=235 at postflight; this movement matches the request result counts but
remains unattributed because concurrent service activity is possible. R2's
private receipts are mode 0600, SHA-256
`0b9e7b07b82e572ecdc7374149d5039fb1c5e785012588c4b345bbd2960602f0`; its ledger
is mode 0600, SHA-256
`d1ef359cab372745c7216020726d38868cd04810cc7b39693c63bb706eac41d8`.

This direct scraper-service slice did not test end-to-end agent scheduling,
progress reporting, cancellation of agent jobs, multiple concurrent jobs,
slow/failing mixed-tier behavior under concurrent load, or synthesis/chunk
complementarity and contradiction handling. Those adoption criteria remain unmeasured. No Jev filter was activated and no deployment or
runtime configuration changed.
