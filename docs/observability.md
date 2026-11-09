# Observability

**Owner:** GroktoCrawl maintainers

| Source of truth | Artifact |
|---|---|
| Prometheus scrape jobs | `docs/prometheus/scrape-config.yml` |
| Prometheus alerts | `docs/prometheus/alerts.yml` |
| Grafana dashboards | `docs/grafana/*-svc-dashboard.json` |
| Incident response | `docs/runbooks/` |

| Service | Metrics endpoint | Prometheus job | Dashboard UID |
|---|---|---|---|
| agent-svc | `http://agent-svc:8080/metrics` | `agent-svc` | `groktocrawl_agent_svc` |
| scraper-svc | `http://scraper-svc:8001/metrics` | `scraper-svc` | `groktocrawl_scraper_svc` |
| semantic-svc | `http://semantic-svc:8003/metrics` | `semantic-svc` | `groktocrawl_semantic_svc` |

| Alert | Runbook |
|---|---|
| HighJobErrorRate | `docs/runbooks/high-job-error-rate.md` |
| QueueDepthSpike | `docs/runbooks/queue-depth-spike.md` |
| ServiceDown | `docs/runbooks/service-down.md` |

Environment-specific target addresses and contact-point secrets are deployment overlays and must never enter this public repository.

`scraper-svc` exports `captcha_attempts_total{provider,strategy,outcome}`. All
labels are bounded provider and strategy constants; URLs, challenge content,
tokens, and screenshots are never metric labels.

## Stage-level telemetry

Performance-stage latency and capacity signals (ADR-0048) are exported with a
`groktocrawl_` prefix and bounded, enum-like label values. Raw URLs, tokens, and
content are never metric labels.

| Service | Metric families |
|---|---|
| agent-svc | `groktocrawl_research_plan_seconds`, `groktocrawl_research_total_seconds{search_type}`, `groktocrawl_research_rank_seconds{mode}`, `groktocrawl_research_gap_detection_seconds`, `groktocrawl_search_query_seconds{engine}`, `groktocrawl_search_queries_total{engine,outcome}`, `groktocrawl_llm_call_seconds{stage}`, `groktocrawl_llm_calls_total{stage,outcome}`, `groktocrawl_research_memory_lookup_seconds`, `groktocrawl_research_memory_lookup_total{outcome}`, `groktocrawl_research_memory_sweep_runs_total`, `groktocrawl_research_memory_orphans_swept_total`, `groktocrawl_research_memory_orphans`, `groktocrawl_scrape_cache_lookup_seconds`, `groktocrawl_scrape_cache_lookup_total{outcome}`, `groktocrawl_active_jobs{type}`, `groktocrawl_jobs_cancelled_total{type}`, `groktocrawl_time_to_first_event_seconds{stream_type}`, `groktocrawl_time_to_first_token_seconds{stream_type}`, `admission_active{class}`, `admission_queue_depth{class}`, `admission_wait_seconds{class}`, `admission_rejected_total{class}`, `admission_cancelled_total{class}` |
| scraper-svc | `groktocrawl_scrape_tier_total{tier,outcome}`, `groktocrawl_scrape_tier_duration_seconds{tier,outcome}`, `groktocrawl_scrape_cache_lookup_seconds`, `groktocrawl_scrape_cache_lookup_total{outcome}`, `groktocrawl_adapter_dispatch_total{adapter_group,outcome}`, `groktocrawl_browser_semaphore_active`, `groktocrawl_browser_semaphore_waiters`, `groktocrawl_browser_semaphore_wait_seconds`, `groktocrawl_browser_setup_seconds`, `groktocrawl_browser_navigation_seconds`, `groktocrawl_browser_extraction_seconds`, `groktocrawl_browser_cleanup_total{outcome}`, `admission_active{class="browser"}`, `admission_queue_depth{class="browser"}`, `admission_wait_seconds{class="browser"}`, `admission_rejected_total{class="browser"}`, `admission_cancelled_total{class="browser"}` |
| browser-svc | `groktocrawl_browser_active_sessions`, `groktocrawl_browser_sessions_destroyed_total{reason}` |

Latency metrics use `_duration_seconds`/`_seconds` (histograms) and outcome
counters use `_total` (counters). The `outcome` label values for
research-memory lookups (`fresh`/`aging`/`stale`/`miss`/`error`) and for
scrape-cache lookups (`hit`/`miss`/`stale`/`error`) are shared with the
cache-freshness model so they compose across services.

## Deduplication and retry counters

`agent-svc` exports two counters so deduplicated fetches and explicit retries
are observable separately. Label values are bounded, enum-like constants; raw
URLs, content, and tokens are never metric labels.

| Metric | Labels | Meaning |
|---|---|---|
| `fetches_deduped_total` | `reason` (`rerank_reuse`) | Scrapes avoided by reusing source content already fetched during ranking |
| `scrape_retries_total` | `stage` (`generic_to_browser`) | Explicit scrape retries by stage transition |

## Hybrid retrieval blend policy

`hybrid_vector` retrieval is planned by `agent-svc/agent/research/hybrid.py`
(ADR-0052). Its discovery branches (SlopSearX and Qdrant) run concurrently and
are independently timeout-bounded (web 15s, vector 8s) inside the
`lightweight_fetch` admission budget. The blend is deterministic and has no
raw-URL metric labels: candidates are normalised and deduplicated, emitted via a
floor guarantee (`floor_web = max(0, limit - vector_unique)`,
`floor_vector = max(0, limit - web_unique)`) followed by round-robin
interleaving. Per-source provenance (`web`/`vector`/`both`) and acquisition
origin (`cache_state`: `from_cache`/`live`, plus `cache_age_ms`) are carried on
the internal `SourceArtifact`, not exported as metric labels.


## Verification

1. Validate the deployment's Prometheus configuration and rules with its native check commands.
2. Confirm all three targets are healthy and query a current metric, for example `up{job="agent-svc"}`.
3. Import or provision the dashboards and confirm their UIDs are loaded.
4. Confirm the alert rules are loaded and test the deployment contact point.
5. Run the supported external API probe:

   ```bash
   groktocrawl --server "$GROKTOCRAWL_API_URL" --json search "observability probe" --limit 1
   ```

## Research-memory cache baselines

Lookup candidates and accepted answer replay are different boundaries. The existing
`groktocrawl_research_memory_lookup_total{outcome}` and
`groktocrawl_research_memory_lookup_seconds` remain the single query-level signals.
Outcomes are `fresh`, `aging`, `stale`, `miss`, `error`, and `cancelled`. These include raw debug
queries and batch queries as well as agent lookups; they are **not an agent request
cache-hit rate**. A stale candidate may still require live research. Unexpected
query exceptions retain their propagation behavior and are counted as `error`,
not misses. Cancellation propagates unchanged and is counted separately as `cancelled`.

Additional bounded metrics distinguish why candidates do not become responses:

| Metric | Labels and meaning |
|---|---|
| `groktocrawl_research_memory_rejections_total` | `boundary=query`: `reason=similarity`, `missing_id`, `missing_artifact`, `invalid_artifact`, `compatibility`, `freshness` (invalid/expired timestamps), or `max_age`. One increment per rejected candidate examined, not per request. |
| Same rejection counter | `boundary=job` or `stream`: `stale_policy` when stale replay is not permitted, `citation` when citation validation rejects replay. Job replay also records `error` for preparation/publication exceptions and `publication` when the job store refuses completion. |
| `groktocrawl_research_memory_bypass_total` | `mode=job` or `stream`, `reason=force_fresh`, `source_content`, or `unavailable` (no memory instance). One increment when the lookup is deliberately skipped. `force_fresh` takes precedence, then `source_content`, then `unavailable`. No backend-health probe is added. |
| `groktocrawl_agent_execution_total` | `mode=job` or `stream`, `path=live` or `replay`, `freshness=none` for live and `fresh`, `aging`, or `stale` for replay. Live counts pipeline starts. Replay counts successful job publication or preparation of the cached SSE `done` event, after citation validation. |
| `groktocrawl_research_memory_replay_seconds` | `mode`, `freshness`: accepted replay preparation through job publication or cached SSE `done` preparation. Excludes memory lookup, webhook delivery, and SWR refresh. Streaming duration includes consumer backpressure while yielding cached tokens. |

At app creation, these counters (including lookup outcomes) expose bounded zero
samples using idempotent zero increments, alongside citation and job counters.
Initialization does not reset existing events or add histogram observations. A
scrape before traffic is still necessary to observe the first rate transition.
Citation correction stays within a single live execution start; its additional
LLM call and outcomes remain visible in citation-recovery and LLM metrics.

All values are fixed categories; no prompts, URLs, user identifiers, models,
fingerprints, memory IDs, or exception messages appear in these labels. Freshness
from an artifact is normalized to the bounded set (unknown becomes `none`). The
query rejection counter stops at the first failed gate for each examined candidate;
one query can reject several candidates and still return a later hit. Candidates
below the first below-threshold result are not visited and are not counted.

Example baseline PromQL:

```promql
sum by (outcome) (rate(groktocrawl_research_memory_lookup_total[5m]))
sum by (boundary, reason) (rate(groktocrawl_research_memory_rejections_total[5m]))
sum by (mode, reason) (rate(groktocrawl_research_memory_bypass_total[5m]))
sum by (mode, path, freshness) (rate(groktocrawl_agent_execution_total[5m]))
sum by (mode) (rate(groktocrawl_research_memory_replay_seconds_sum[5m]))
/ sum by (mode) (rate(groktocrawl_research_memory_replay_seconds_count[5m]))
```

The final expression is mean accepted replay duration, not a percentile. Histogram
percentiles above the largest finite bucket are censored and must not be displayed
as an exact tail-latency estimate.

These are execution-boundary baselines, not SLOs, delivery acknowledgments, or
unique-request success denominators. Job retries can start the live pipeline more
than once. Existing error fallbacks after publication may also start live work;
these metrics do not change that behavior. An SSE consumer disconnecting before
`done` is not an accepted replay in this counter, and yielding `done` does not prove
client receipt. SWR background refresh is excluded from the agent execution counter
and retains normal underlying research-stage telemetry. Cache-served jobs still
bypass the normal worker lifecycle instrumentation; the dedicated replay timing
does not repair or claim parity with `job_duration_seconds`/active-job metrics.
