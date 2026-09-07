# Scaling scraper acquisition

The opt-in topology supports **one API process and 1–4 scraper replicas**.
Use Docker Compose 2.24.4 or newer. Provision up to 2 CPUs, 2500 MB and 512 PIDs
per scraper replica, plus the existing services and gateway.

```sh
docker compose -f docker-compose.yml -f docker-compose.scaleout.yml up -d --build --scale scraper-svc=2
```

The scraper host port now belongs to `scraper-gateway`. Scraper replicas have
no published ports; the API uses `http://scraper-gateway:8001`. The gateway
discovers up to four replicas, checks `/health`, routes by least connections,
and times out queued requests after five seconds. It never retries a failed
scrape POST automatically. Upstream connections close after a response so
long-lived API keepalive connections do not pin all calls to one replica.

| Resource | Bound |
| --- | --- |
| Browser lifecycles | 4 per scraper; at most 16 across 4 replicas |
| API browser admission | 128 weighted units / weight 8 = 16 operations |
| API lightweight fetch admission | 64 operations |
| API LLM admission | 32 weighted units / weight 4 = 8 calls |
| Gateway active connections | 128 total; 32 per backend |
| Gateway queued connections | 32 per backend; 5-second queue timeout |

These are separate bounds: a generic scrape can escalate to a browser at the
scraper service. Physical browser limits still apply. Per-request concurrency
and origin pacing remain in effect. Increasing replicas may improve aggregate
throughput more than the latency of one session.

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
real gateway at 1/2/4 replicas using a bounded 100 ms acquisition twin. Their
results establish routing/capacity behavior, not real-browser performance. Use
the browser-heavy production fixtures for the final hardware-specific sizing.

Rollback by returning to the base Compose file in a maintenance window; this
reassigns the published scraper port and recreates the API's scraper endpoint.
