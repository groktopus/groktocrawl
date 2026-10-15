# Bounded memory and process study — September 30

**Decision: retain the init-reaper repair and existing memory limits.** The declared serial workload passed, with short-term resource stabilization within the preregistered tolerances. This does not rule out slow leaks or qualify heavier/concurrent workloads.

## What changed

Two pre-repair attempts completed no workload. Browser creation failed with HTTP 502 / backend `BlockingIOError(11)`. The [read-only diagnosis](process-diagnostic.json) found 134 zombie browser processes under PID 1 and a saturated task limit, despite healthy status. Merged PR #397 (`ec9d1af501d127536f25c536bd2a0daf3a4ef55e`) enables init for both repository browser Compose definitions. Only the experimental browser container was recreated, with no active sessions. Its init setting and container health were verified. Mainline deployment and Hermes configuration were unchanged.

The agent remained at runtime revision `46a528228b1365189cdd38d0bcdb12109a8dc763`, model `free`. This was a new post-repair configuration baseline; completed W9 packets remain historical evidence, not qualification of this changed configuration.

## Results

The [protocol](protocol.md) was committed and pushed before measurement. The [post-repair receipt](post-repair.json) contains six blocks, sixty browser DOM/create/read/delete cycles, sixty equivalent public-document scrapes, six idle samples and 73 total observations over 338.32 seconds. No model calls were requested. [Integrity verification](integrity.json) confirms the receipt hash and numeric resource-field allowlist; credentials, endpoint URLs, personal paths and container identifiers are absent.

| Measurement | Browser | Scraper |
|---|---:|---:|
| Baseline total memory (MiB) | 74.496 | 1305.898 |
| Settled block 1 total memory (MiB) | 151.254 | 1306.832 |
| Settled block 6 total memory (MiB) | 153.473 | 1306.250 |
| Settled total-memory spread (MiB) | 2.219 | 1.215 |
| Settled anonymous-memory spread (MiB) | 2.028 | 0.008 |
| Final idle total memory (MiB) | 164.551 | 1306.504 |
| Peak sampled limit usage | 10.970% | 52.286% |
| Peak sampled task count | 13 | 200 |
| Zombie processes / new OOM events | 0 / 0 | 0 / 0 |

All sampled browser active-session counts were zero after cleanup. Container identity/start/restart guards passed. Browser counters independently showed sixty created and sixty deleted sessions; no extra browser lifecycle was observed during the workload. The scraper already used init and its read-only pre-repair check also found zero zombies.

## Interpretation and limits

Settled spreads were below the preregistered 32 MiB stabilization tolerance. No settled monotonic increase exceeded the 64 MiB investigation threshold. Browser anonymous memory nevertheless rose by about 2 MiB across settled blocks: a small positive trend is retained, not erased by the pass threshold. Most initial total-memory warming was file cache (18.102 to 86.824 MiB), which then remained stable.

Final browser idle anonymous memory was 70.957 MiB, higher than the last settled 60.680 MiB, with one additional sampled task. Cgroup samples include monitoring and container-health processes; this study does not establish the cause of that idle increase or show full reclamation. RSS can double-count shared pages and should not be substituted for cgroup total usage.

This is one short serial run, not six independent experiments. Synthetic browser DOM lifecycle and repeated scraper capture are distinct strata. Scraper cache/content negotiation may avoid Playwright; retrieval-tier attribution was not captured, so these results do not qualify all scraper fallback paths. Real navigation, barriers, sustained concurrent research and day-scale growth remain outside the study. Pre-repair failures are retained with zero credit. The separate [process-health repair and live check](../browser-process-health/results.md) completed #398 after this study.

The bounded #392 acceptance is satisfied: the declared repeated workload stabilized within its declared tolerance, and the observed process-exhaustion defect was repaired and exercised. Retain memory limits and init; do not raise limits to mask lifecycle faults. The subsequent #398 repair adds degraded-health reporting; investigate again if real workload growth or failed cleanup appears.
