# Interrupted Jobs

Owner: GroktoCrawl maintainers

## When to use

After an `agent-svc` restart, crash, forced termination, or deployment, jobs that were in flight may remain `processing` until their 24-hour record TTL expires. Background execution is best-effort and not restart-safe (see [ADR-0047](../adr/0047-defer-restart-safe-execution.md)): interrupted work is not resumed, partial artifacts are not rolled back, and undelivered completion or failure webhooks are not replayed. There is **no durability SLO** for in-flight execution.

## Symptoms

- Jobs stuck in `processing` well beyond their expected runtime. Crawl jobs are bounded by `CRAWL_MAX_DURATION_SECONDS` (default 1800s) and `CRAWL_IDLE_TIMEOUT_SECONDS` (default 300s).
- Clients polling job status never reach `completed` or `failed`.
- `agent-svc` restarted, was killed, or was redeployed while jobs were in flight.
- Completion or failure webhooks were not received after a restart.

## Identify stranded jobs

1. **List processing jobs via the API:**

   ```bash
   curl -s http://localhost:8080/v2/activity
   ```

   Crawl-only: `curl -s http://localhost:8080/v2/crawl/active`.

2. **Flag jobs older than a threshold** (dry-run by default — nothing is modified):

   ```bash
   docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py --stale-after 3600
   ```

   Lower the threshold right after a known restart (e.g. `--stale-after 60`) to sweep jobs orphaned by that restart. Filter by kind with `--kind crawl|agent|extract|batch_scrape|llmstxt|plan_execute`; add `--json` for machine-readable output. The tool exits `1` when stranded jobs are found, so it can be used as an alerting check.

3. **Raw Valkey inspection (fallback, no Python client needed):**

   ```bash
   docker compose exec valkey valkey-cli --scan --pattern 'job:*:meta'
   docker compose exec valkey valkey-cli GET 'job:<job_id>:meta'
   ```

   Each record includes `status`, `created_at`, and `kind`. A `processing` record whose `created_at` predates the restart is stranded.

## Resolve interrupted jobs

1. **Where a cancel endpoint exists, cancel instead of failing:**

   ```bash
   curl -X DELETE http://localhost:8080/v2/crawl/<job_id>
   curl -X DELETE http://localhost:8080/v2/agent/<job_id>
   curl -X DELETE http://localhost:8080/v2/batch/scrape/<job_id>
   ```

   Extract, llmstxt, and plan-execution jobs have no cancel endpoint — use step 2.

2. **Reconcile stranded jobs (processing -> failed only):**

   ```bash
   docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py --stale-after 3600 --fail
   ```

   The tool re-reads each record immediately before writing and only transitions records still in `processing`, so a job that completed or was cancelled in the meantime is never overwritten. Re-run the dry-run to confirm nothing remains in `processing`.

3. **Re-submit critical work.** The original request is not replayed; re-run the client command (e.g. `./groktocrawl crawl ...`). Webhook consumers should deduplicate by `webhookId` — a re-submitted job delivers a fresh event with a fresh `webhookId`.

4. **Let expiry handle the rest.** Records TTL out 24 hours after creation; reconciled `failed` records preserve the original TTL.

## Prevention

- Deploy and restart with graceful shutdowns (`docker compose stop`, `docker compose up -d --force-recreate`) so in-flight tasks get the five-second ADR-0035 grace period; avoid `docker compose kill` and `SIGKILL` unless necessary.
- Alert on stranded jobs: run `scripts/reconcile-jobs.py --stale-after <threshold>` on a schedule and page on exit code `1`, or monitor `GET /v2/activity` after known restart events.
- Track durable-execution milestones in [ADR-0047](../adr/0047-defer-restart-safe-execution.md) (leases, reconciliation surface, webhook outbox, queue-backed worker, artifact consistency) and re-evaluate when restart incidents become frequent.

## Escalation

- **Level 1 (On-call engineer)**: reconcile stranded jobs as above and re-submit critical work.
- **Level 2 (Service owner)**: if jobs keep stranding on every restart, or partial artifacts are corrupting downstream stores (Qdrant/semantic index, research memory), escalate to the service owner and review the durability roadmap in ADR-0047.
