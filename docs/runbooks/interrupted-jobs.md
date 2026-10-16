# Interrupted Jobs

Owner: GroktoCrawl maintainers

## When to use

After an `agent-svc` restart, startup recovery dispatches jobs whose versioned Valkey marker proves they were still queued and never claimed. Work that had started is never replayed. After its 45-second lease expires, the record becomes `failed` with `error_code=OUTCOME_UNKNOWN`; a provider or downstream service may already have completed the operation. Pre-marker records follow the same fail-closed rule. Partial artifacts are not rolled back. Completion/failure webhook intents are persisted and retried at-least-once with the original `webhookId`; receivers must deduplicate it. See [ADR-0072](../adr/0072-valkey-leased-legacy-job-recovery.md).

## Symptoms

- A job remains `processing` well beyond startup and the 45-second execution lease.
- A job changes to `failed` with `OUTCOME_UNKNOWN` after an interrupted worker loses its lease.
- Clients polling job status never reach `completed` or `failed`.
- `agent-svc` restarted, was killed, or was redeployed while jobs were in flight.
- Completion or failure webhooks were not received after a restart.

## Identify stranded jobs

1. **List processing jobs via the API:**

   ```bash
   curl -s http://localhost:8080/v2/activity
   ```

   Crawl-only: `curl -s http://localhost:8080/v2/crawl/active`.

2. **Check for stale records after startup recovery** (dry-run by default — nothing is modified):

   ```bash
   docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py --stale-after 3600
   ```

   Startup recovery should redispatch safe queued records and fail interrupted active records after their lease expires. If `processing` records remain after that window, use the tool to investigate; do not resubmit them until the external effect is checked. Filter by kind with `--kind crawl|agent|extract|batch_scrape|llmstxt|plan_execute`; add `--json` for machine-readable output. The tool exits `1` when stranded jobs are found.

3. **Raw Valkey inspection (fallback, no Python client needed):**

   ```bash
   docker compose exec valkey valkey-cli --scan --pattern 'job:*:meta'
   docker compose exec valkey valkey-cli GET 'job:<job_id>:meta'
   ```

   Records include an internal `execution` marker. `state=queued` is eligible for safe redispatch; `state=running` with an expired lease is not replayed. A missing marker means the job predates this recovery protocol and will be marked `OUTCOME_UNKNOWN`.

## Resolve interrupted jobs

1. **For `OUTCOME_UNKNOWN`, inspect before resubmitting.** Check provider or downstream logs and any partial job artifacts to determine whether the requested effect already happened. A new submission receives a new job ID and can repeat those effects.

2. **For a still-live job, use its cancel endpoint:**

   ```bash
   curl -X DELETE http://localhost:8080/v2/crawl/<job_id>
   curl -X DELETE http://localhost:8080/v2/agent/<job_id>
   curl -X DELETE http://localhost:8080/v2/batch/scrape/<job_id>
   ```

   Extract, llmstxt, and plan-execution jobs have no cancel endpoint.

3. **Use manual reconciliation only as a fallback** if startup recovery leaves a stale `processing` record. Stop `agent-svc` first:

   ```bash
   docker compose exec agent-svc python3 /app/scripts/reconcile-jobs.py --stale-after 3600 --fail --assume-stopped
   ```

   `--assume-stopped` is required because a live worker must not be failed manually. The normal lease recovery path uses atomic fenced transitions; the reconciliation script remains a best-effort operator fallback.

4. **Re-submit only after checking the effect.** The original request is never replayed after a worker has started. A new job has a new ID and webhook event IDs.

5. **Let expiry handle the rest.** Job and outbox records expire 24 hours after creation.

## Prevention

- Deploy and restart with graceful shutdowns (`docker compose stop`, `docker compose up -d --force-recreate`) so in-flight tasks get the five-second ADR-0035 grace period; avoid `docker compose kill` and `SIGKILL` unless necessary.
- Alert on records that remain `processing` beyond the lease window and monitor `OUTCOME_UNKNOWN` failures after restarts.
- Review the bounded recovery contract and remaining cross-service artifact-consistency limitation in [ADR-0072](../adr/0072-valkey-leased-legacy-job-recovery.md).

## Escalation

- **Level 1 (On-call engineer)**: reconcile stranded jobs as above and re-submit critical work.
- **Level 2 (Service owner)**: if jobs keep stranding on every restart, or partial artifacts are corrupting downstream stores (Qdrant/semantic index, research memory), escalate to the service owner and review the bounded recovery contract and remaining artifact-consistency limits in [ADR-0072](../adr/0072-valkey-leased-legacy-job-recovery.md). ADR-0047 is the historical record of the earlier best-effort decision.
