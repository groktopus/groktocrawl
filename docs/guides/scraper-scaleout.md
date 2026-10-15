# Scaling scraper acquisition

The opt-in topology supports **one API process and 1–4 scraper replicas**.
Use Docker Compose 2.24.4 or newer. Each replica is configured with a one-CPU
quota, 16 browser lifecycles, a warm browser pool, 2500 MB and 512 PIDs. Provision
enough host CPU and memory for the replica count, plus the other services and gateway.
This is the bounded experimental configuration from [X ADR-0089](../adr/experimental/groktocrawl-x/0089-bound-browser-concurrency-per-effective-cpu.md),
not a production hardware sizing guarantee.

```sh
python3 deploy/scaleout.py --replicas 2
```

The launcher sets `ADMISSION_BROWSER_LIMIT` to `replicas × 16 × 8` weighted units
and rejects a conflicting explicit value. For example, two replicas receive
256 units, permitting 32 explicitly browser-classified API operations. Use the
launcher when changing replica count so the API budget changes with the physical
browser capacity. `--dry-run` prints the plan without starting Docker, and
`--no-build` uses existing images.

Changing the count on a running stack can recreate `agent-svc` because its
admission environment changes; active inline jobs are not restart-safe.
Drain or finish those jobs first and pass `--allow-api-restart`. For downscale,
also drain the removed gateway backends using the procedure below, then pass
`--confirm-drained-downscale`. The launcher will refuse these changes without
those explicit acknowledgments; it does not perform the drains for you.

The scraper host port now belongs to `scraper-gateway`. Scraper replicas have
no published ports; the API uses `http://scraper-gateway:8001`. The gateway
discovers up to four replicas, checks `/health`, routes by least connections,
and times out queued requests after five seconds. It never retries a failed
scrape POST automatically. Upstream connections close after a response so
long-lived API keepalive connections do not pin all calls to one replica.

| Resource | Bound |
| --- | --- |
| Browser lifecycles | 16 per one-CPU scraper replica; at most 64 across 4 replicas |
| API browser admission | 128/256/384/512 weighted units for 1/2/3/4 replicas; weight 8 per forced-browser operation |
| API lightweight fetch admission | 64 operations |
| API LLM admission | 32 weighted units / weight 4 = 8 calls |
| Gateway active connections | 128 total; 32 per backend |
| Gateway queued connections | 32 per backend; 5-second queue timeout |

These are separate bounds: a generic scrape can escalate to a browser at the
scraper service after entering the API as a lightweight fetch. Physical browser
limits still apply, and API admission is not an exact reservation of downstream
browser capacity for such escalations. Per-request concurrency and origin pacing
remain in effect. Increasing replicas may improve aggregate throughput more
than the latency of one session. This capacity change does not remove the
research planner's operational controls. The experimental agent acquisition
policy admits every distinct URL returned by SlopSearX; it has no separate
default source-attempt cap. An explicit caller-selected `max_credits` remains
a narrower attempt budget when provided. See
[X ADR-0092](../adr/experimental/groktocrawl-x/0092-retire-per-query-search-maximum-and-acquire-all-distinct-results.md).

The one-core benchmark pinned each replica to a separate physical P-core.
Compose's one-CPU quota does **not** pin a replica to an exclusive core. A busy,
oversubscribed, fractional, or heterogeneous host can have different latency;
inspect effective CPU quotas, browser wait time, p95/p99 latency, errors, RSS,
and PIDs before treating 16 slots as usable capacity. The pool can retain up
to two domain-fingerprinted browser processes per replica; mixed-domain sites
may use more memory than the single-domain benchmark.

Do not use `--scale agent-svc` or more than four scraper replicas with this
profile. Multiple API processes need coordinated or explicitly partitioned
budgets, and browser-svc interactive sessions have different ownership rules.
The fixture API is a testing alternative, not a second production API process.

Distributed origin pacing is enabled and requires Valkey. It atomically reserves
slots across replicas with the server clock, rejects waits over 30 seconds, and
fails closed when shared coordination is unavailable. A cancelled reservation
can leave a bounded unused slot; it is not reclaimed ahead of other callers.

## Validation and rollout

Compare 1, 2 and 4 replicas using the same fixture corpus and offered load.
Report one-request and many-session p50/p95 latency separately, throughput,
admission/browser queue wait, error rate, source quality, RSS and PIDs. First
verify traffic reaches each healthy replica. More replicas cannot remove
planning/synthesis latency or provider limits.

Scale up first, wait for readiness checks to pass, then increase offered load.
For downscale/replacement, drain the affected backend in the gateway before
stopping it and allow active requests to finish. The 75-second process grace
period alone is not a drain guarantee. An abrupt failed backend can fail active
requests; the gateway deliberately avoids duplicate expensive retries. Running
agent jobs remain best-effort across restarts under ADR-0047.

The gateway's runtime API binds only its own loopback interface. Use `show
servers state` to map the selected replica address to its backend slot; then
drain that slot and wait for its active session count (`scur` in `show stat`)
to reach zero before stopping the container. For example, with slot `scraper1`:

```sh
printf 'set server scrapers/scraper1 state drain\n' | docker compose -f docker-compose.yml -f docker-compose.scaleout.yml exec -T scraper-gateway nc 127.0.0.1 9999
```

Restore a temporarily drained healthy slot with `state ready`. For a rollout,
verify DNS convergence and readiness of its replacement before draining another.

`benchmarks/scraper_scaleout.py` and the Scraper Scale-Out workflow exercise the
real gateway and resolved Compose capacity settings at 1/2/4 replicas using a
bounded 100 ms acquisition twin. Their results establish routing/configuration
behavior, not real-browser performance. Use representative browser-heavy load
and a sustained soak test for final hardware-specific sizing. The short
[X ADR-0089 evidence](../adr/experimental/groktocrawl-x/0089-bound-browser-concurrency-per-effective-cpu.md)
supports the experimental ceiling, not automatic production adoption.

Rollback by returning to the base Compose file in a maintenance window; this
reassigns the published scraper port and recreates the API's scraper endpoint.
