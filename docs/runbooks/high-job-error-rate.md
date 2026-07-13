# Alert Name

HighJobErrorRate

Owner: GroktoCrawl maintainers

## Severity

Critical

## Symptoms

- The error rate for a specific job type (e.g., `crawl`, `search`, `scrape`) has exceeded 0.1 errors per second over a 5-minute window.
- Users may report incomplete or failed agent research jobs.
- The agent-svc logs show repeated error-level entries for `jobs_failed_total` increments.
- `/metrics` at agent-svc shows elevated `jobs_failed_total{type="..."}` counts.

## Immediate Actions

1. **Acknowledge the alert** in your monitoring system to prevent duplicate escalation.
2. **Check current error rate** by querying the agent-svc `/metrics` endpoint:
   ```bash
   curl -s http://localhost:8080/metrics | grep jobs_failed_total
   ```
3. **Identify the failing job type** from the alert labels — note which `type` is failing.
4. **Check service health** for all dependencies:
   ```bash
   curl -s http://localhost:8080/health
   ```
   Look for any dependency in a non-ok state (valkey, slopsearx, scraper, browser, portal).

## Investigation Steps

1. **Review agent-svc logs** for the affected job type:
   ```bash
   docker compose logs agent-svc --tail=200 | grep -i "error\|failed\|exception"
   ```
2. **Correlate with dependency health** — check if upstream services (scraper-svc, llm-svc) are healthy:
   ```bash
   docker compose ps
   curl -s http://localhost:8001/health  # scraper-svc
   ```
3. **Check resource utilization** — look for memory or CPU pressure on the agent-svc container:
   ```bash
   docker compose stats --no-stream agent-svc
   ```
4. **Verify Valkey connectivity** — if jobs cannot store state, failures cascade:
   ```bash
   docker compose exec valkey redis-cli PING
   ```
5. **Inspect error patterns** — are errors consistent (e.g., timeouts, connection refused, parsing errors)? Group them by message to identify root cause.
6. **Check for recent deployments** — correlate the alert onset with recent code changes or configuration updates.

## Escalation Path

- **Level 1 (On-call engineer)**: Follow immediate actions and investigation steps. If the error rate is caused by a transient dependency issue, restart the dependency and monitor for recovery.
- **Level 2 (Service owner)**: If the error rate persists beyond 15 minutes or is caused by a code defect, escalate to the team owning the affected job type. File a bug with logs and failing job type labels.
- **Level 3 (Engineering leadership)**: If the error rate affects all job types or is caused by a systemic issue (e.g., valkey outage, resource exhaustion), notify engineering leadership and consider scaling the service or rolling back recent changes.
