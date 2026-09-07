# BrowserPool concurrency evidence

Run on 2026-09-04 with the production `BrowserPool` and Playwright on the
local macOS host. The benchmark fulfills every page through Playwright route
interception, so it makes no origin or external network requests. Each
synthetic session runs three page fetches with at most three contexts in
parallel. The 1, 2, and 4 pool cases use independent `BrowserPool` instances,
each with `max_processes=1` and a four-context admission semaphore matching
the scraper's default `SCRAPER_MAX_BROWSER_CONCURRENCY`. The eight-session
case assigns sessions round-robin across those pools.

The raw measurements are in
[`browser_pool_concurrency-results.json`](browser_pool_concurrency-results.json).

| Pools | Sessions | Pages | Throughput (pages/s) | Session p50/p95 (s) | Admission p50/p95 (s) | Pool acquire p50/p95 (s) | Peak descendant RSS / PIDs |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 3 | 0.988 | 2.560 / 2.560 | 0.000 / 0.000 | 1.046 / 1.065 | 1,048 MiB / 12 |
| 1 | 8 | 24 | 4.877 | 3.655 / 4.790 | 2.982 / 4.396 | 0.045 / 0.345 | 1,295 MiB / 13 |
| 2 | 1 | 3 | 1.551 | 1.817 / 1.817 | 0.000 / 0.000 | 0.358 / 0.359 | 1,020 MiB / 11 |
| 2 | 8 | 24 | 5.937 | 3.468 / 3.884 | 2.297 / 3.390 | 0.079 / 0.444 | 2,347 MiB / 25 |
| 4 | 1 | 3 | 1.583 | 1.825 / 1.825 | 0.000 / 0.000 | 0.363 / 0.364 | 1,002 MiB / 11 |
| 4 | 8 | 24 | 5.493 | 3.944 / 4.189 | 0.000 / 3.263 | 0.555 / 0.630 | 4,013 MiB / 56 |

All 24-page cases returned identical content hashes, and every pool reported
zero processes after `finally` cleanup. The one-process-per-pool bound was
held throughout; the process-tree samples include Playwright's driver and
Chromium descendants. The benchmark's four-context-per-pool admission bound
matches the production default, while its one-process-per-pool setting is
lower than the BrowserPool default of two to keep this probe within four
browser processes. RSS, PID, and CPU values are best-effort snapshots from
`ps`, so they are directional rather than a resource contract.

These results show bounded local pool behavior and context concurrency. They
do not measure Docker replica routing, the scraper HTTP server, full research
sessions, provider latency, LLM work, or production browser capacity. They
must not be added to the gateway-twin throughput numbers as an aggregate
production claim.

Command:

```sh
PYTHONPATH=agent-svc:scraper-svc:llm-svc:. \
  python \
  benchmarks/browser_pool_concurrency.py \
  --output benchmarks/browser_pool_concurrency-results.json
```
