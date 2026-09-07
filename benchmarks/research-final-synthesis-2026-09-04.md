# Final research synthesis probe — 2026-09-04

Run `PYTHONPATH=agent-svc:scraper-svc:. python benchmarks/research_final_synthesis.py`. Ten samples compare the actual baseline loop at `ca591199cfd05144e45d2372afd659f2e86e6327` with the revised loop. Search and fetch each wait 10 ms, gap analysis waits 20 ms, synthesis waits 60 ms. External clients are replaced with deterministic stubs; both versions return the same two source URLs. p95 uses nearest-rank. These timings isolate orchestration, not production/model quality.

| Loop | Streaming | p50 total ms | p95 total ms | p50 first token ms | Synthesis calls | Tokens equal final answer |
|---|---|---|---|---|---|---|
| baseline | False | 188.87 | 192.77 | None | 2 | None |
| final_only | False | 127.38 | 129.86 | None | 1 | None |
| baseline | True | 188.63 | 190.18 | 83.33 | 2 | False |
| final_only | True | 127.96 | 128.75 | 126.92 | 1 | True |

The first token arrives later because it belongs to the final answer. Total work drops by one generation, with no added simultaneous model calls. Token usage reduction depends on the discarded draft length; this fixture does not measure real provider tokens or quality. Behavioral tests verify combined evidence, no-gap early exit, credit exhaustion, schema mode, provider errors, and cancellation/client cleanup. Grounded quality evaluation and real provider contention remain deployment measurements.
