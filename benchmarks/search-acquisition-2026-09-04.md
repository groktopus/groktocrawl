# Search acquisition probe — 2026-09-04

Run `PYTHONPATH=agent-svc:. python benchmarks/search_acquisition.py`. Ten samples per case, each fetch sleeps 20 ms; no network or LLM. Previous scheduling simulates sequential ranking, five rich fetches, and two contents fetches. Shared scheduling calls the production acquisition helper three times with retained artifacts. p95 is nearest-rank (maximum of ten samples). These isolate scheduling and reuse, not production end-to-end speedups.

| Sources | Shared artifacts | Fetches | Peak fetches | p50 ms | p95 ms |
|---|---|---|---|---|---|
| 1 | False | 3 | 1 | 63.12 | 63.59 |
| 1 | True | 1 | 1 | 21.37 | 21.74 |
| 5 | False | 15 | 5 | 189.05 | 194.18 |
| 5 | True | 5 | 5 | 21.37 | 22.06 |
| 10 | False | 30 | 5 | 356.06 | 358.21 |
| 10 | True | 10 | 5 | 42.76 | 43.77 |

Service regression tests additionally cover hybrid + rich + contents in one HTTP request, refusal and option compatibility, distinct URL identities, progressive SSE completion, and cancellation cleanup.
