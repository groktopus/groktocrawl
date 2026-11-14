# Browser quarantine recovery

`/health`503 with admission reason `cleanup_incomplete` means uncertain teardown,
not necessarily current memory/PID exhaustion. Inspect cleanup stage/outcome
logs and `groktocrawl_browser_cleanup_incomplete_total` with timestamped resource
telemetry. Counters alone do not establish the crash cause.

Standard Compose enables `BROWSER_CONTAINER_RECYCLE_ON_QUARANTINE=1`, requires
one uvicorn process directly under PID 1 init, private container PID namespace,
and `restart: unless-stopped`. Do not enable in host, multi-worker, reloader or
persistent supervisor deployments. The first quarantine starts a fixed 30-second
grace then exit 75; Docker destroys remaining processes before replacement.
No ownership is cleared in-place and no requests are replayed. Active sessions
may disconnect and their IDs are invalid after restart.

During rollout, test recovery in an isolated container, not by forcing a
production failure. Verify restart count increments and the old host PIDs are
gone. Production smoke: health 200; create session; navigate a known-safe page;
read content; delete; health shows zero reserved sessions and no quarantine.

Alert on repeated cleanup failures/restarts. Disable the flag (`0`) and recreate
only browser-svc if recycling repeats; collect stage diagnostics and resource
telemetry. Do not solve uncertainty by clearing owner records or lifting limits.
For disabled mode, an authorized manual restart destroys uncertain resources.

Rollback uses the saved Compose preimage and prior image digests for browser-svc
and scraper-svc only; preserve other services. No storage migration or automatic
job replay is needed. See ADR 0080 and the interrupted-jobs runbook for session
loss versus durable job uncertainty.
