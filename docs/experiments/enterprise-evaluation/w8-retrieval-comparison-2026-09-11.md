# W8 retrieval channel comparison — 2026-09-11

Status: **comparison complete; retain current SlopSearX path and proceed to bounded query planning**

Three retrieval scopes received the same frozen 24 queries, 20-result limit, 30-second deadline, no retries, and an empty observed response cache. The comparison asks whether broader engine fan-out can repair the current exact known-source misses before GroktoCrawl-X adds adaptive planning.

| Scope | Completed | Known URLs | Queries with match | Mean first rank | p50 / p95 |
|---|---:|---:|---:|---:|---:|
| Current default | 24/24 | 17/44 | 14/24 | 2.79 | 551 / 1,103 ms |
| Broad + authoritative specialists | 24/24 | 17/44 | 14/24 | 2.79 | 984 / 2,063 ms |
| Specialists without broad web | 24/24 | 0/44 | 0/24 | — | 225 / 335 ms |

The broad-plus-specialist scope added no exact known-source matches and increased median and tail latency by about 1.8 times. The specialist-only scope was fast but found none of the frozen target URLs from the unchanged natural-language questions. A prior specialist attempt was invalidated when it started inside the service's cumulative rate-limit window; all attempts remain private and the clean rerun is the reported result.

The default is therefore the best of these three fixed-query scopes, but it is not a satisfactory final acquisition policy. Every arm was partial. The same production engine problems remained visible: OpenAlex errors, Semantic Scholar and arXiv rate limits, and little or no useful output from other selected specialists. Broad default retrieval was effectively carried by Brave because DuckDuckGo, Google, Reddit, and Wikipedia were frequently blocked or limited.

Exact URL matching also understates useful retrieval. The default results sometimes found an equivalent current documentation route, another representation of the same paper, or a relevant primary paper outside the frozen known-URL list. The fixed known-source measure remains the reproducible comparison; these observations are reasons for a blinded pooled relevance audit, not permission to rewrite judgments after seeing an arm.

## Decision

Do not expand default fan-out. Keep current SlopSearX as the acquisition boundary while W8 tests bounded query formulation, source-aware routing, and stopping. The next policy may spend extra calls only for a declared missing evidence need, must retain every attempt and engine failure, and must stop at the application-owned budget. This is the experiment in issue #295.

SlopSearX #307 remains justified for general-purpose provenance, canonical identity, rank explanation, timestamps, and diagnostics. Its proposed features would improve measurement and handoff, but SlopSearX should not decide research sufficiency or embed GroktoCrawl-specific planning.

## Private evidence

| Run | Freeze digest |
|---|---|
| Current default | `sha256:364f78ded1676ec38bc3cd59d515288cb5d98693b16a11dcd2cde7efe2f832cf` |
| Broad + specialists | `sha256:95d1552e9edd9c22884f664dad4d67fc108c80171947a68294431c62a8876275` |
| Specialist-only clean rerun | `sha256:0783bfe9e0d7a6550e8548366f9d39c7740f7efacc99aff0ad905f59d193e815` |

No production configuration or adoption decision changes from this comparison.
