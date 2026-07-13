# Alert Name

QueueDepthSpike

Owner: GroktoCrawl maintainers

## Severity

Warning

## Symptoms

- The `queue_depth` gauge in agent-svc `/metrics` exceeds 100 active jobs.
- Job processing latency increases as the backlog grows.
- Users may experience delayed responses or timeouts on agent research requests.
- The agent-svc logs may show increased queue wait times.

## Immediate Actions

1. **Acknowledge the alert** in your monitoring system.
2. **Check current queue depth**:
   ```bash
   curl -s http://localhost:8080/metrics | grep queue_depth
   ```
3. **Identify the cause of the backlog** — check if jobs are processing slowly or failing:
   ```bash
   curl -s http://localhost:8080/metrics | grep -E "jobs_(completed|failed)_total"
   ```
4. **Assess dependency health** — slow upstream services can cause jobs to pile up:
   ```bash
   curl -s http://localhost:8080/health
   ```
5. **If the backlog is growing rapidly and dependencies are healthy**, identify the slow job type and recent configuration or deployment changes.

## Investigation Steps

1. **Review job processing metrics** to determine whether jobs are completing, failing, or stuck:
   ```bash
   curl -s http://localhost:8080/metrics | grep -E "jobs_(submitted|completed|failed)_total|job_duration_seconds"
   ```
2. **Check for slow dependencies** — spike in scrape or search duration can cause queues to build:
   ```bash
   curl -s http://localhost:8001/metrics | grep scrape_duration_seconds
   ```
3. **Inspect agent-svc logs for bottlenecks**:
   ```bash
   docker compose logs agent-svc --tail=200 | grep -i "duration\|timeout\|slow\|retry"
   ```
4. **Check LLM latency** — if the LLM endpoint is slow, all research jobs will queue:
   ```bash
   curl -s http://localhost:8080/metrics | grep http_request_duration_seconds
   ```
5. **Verify resource utilization** — CPU or memory contention can slow job processing:
   ```bash
   docker compose stats --no-stream agent-svc
   ```
6. **Determine if the spike is traffic-driven** — check for a corresponding increase in `jobs_submitted_total` rate. If submissions are normal but queue is growing, the bottleneck is processing speed.
7. **Review recent deployments or configuration changes** that may have introduced a performance regression.

## Escalation Path

- **Level 1 (On-call engineer)**: Follow immediate actions. If the queue depth is driven by a transient traffic spike and dependencies are healthy, monitor for 10 minutes. The backlog should clear naturally as jobs complete.
- **Level 2 (Service owner)**: If the queue depth exceeds 200 or persists for more than 15 minutes despite healthy dependencies, escalate to the service owner. Investigate potential performance regressions or resource bottlenecks.
- **Level 3 (Engineering leadership)**: If the queue depth spike is caused by a systemic issue (e.g., all jobs are stuck due to a code defect, or the LLM provider is degraded), escalate to engineering leadership.
