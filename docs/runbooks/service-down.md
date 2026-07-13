# Alert Name

ServiceDown

Owner: GroktoCrawl maintainers

## Severity

Critical

## Symptoms

- The `up` metric for `agent-svc`, `scraper-svc`, or `semantic-svc` is `0`, indicating the service is unreachable by Prometheus.
- Users cannot access the affected service's API endpoints.
- Health checks for the affected service return connection refused or timeout.
- Downstream services that depend on the affected service may also exhibit failures.

## Immediate Actions

1. **Acknowledge the alert** in your monitoring system.
2. **Verify the service status** directly:
   ```bash
   docker compose ps <service-name>
   ```
   Replace `<service-name>` with `agent-svc`, `scraper-svc`, or `semantic-svc`.
3. **Check service logs** for crash indicators or startup failures:
   ```bash
   docker compose logs --tail=100 <service-name>
   ```
4. **Restart the service** if it has crashed:
   ```bash
   docker compose up -d --force-recreate <service-name>
   ```
5. **Verify service recovery**:
   ```bash
   curl -s http://localhost:<port>/health
   ```
   - agent-svc health: `http://localhost:8080/health`
   - scraper-svc health: `http://localhost:8001/health`
   - semantic-svc health: `http://localhost:8003/health`

## Investigation Steps

1. **Determine the cause of the crash** by examining the last log lines before the service stopped:
   ```bash
   docker compose logs --tail=200 <service-name>
   ```
2. **Check for OOM kills** — look for `Killed` or `OOM` in docker logs:
   ```bash
   docker inspect "$(docker compose ps -q <service-name>)" --format '{{.State.OOMKilled}}'
   ```
3. **Verify Docker host resources** — insufficient memory or disk space can cause service termination:
   ```bash
   docker info | grep -i memory
   df -h
   ```
4. **Check for port conflicts** — another process may be using the service's port:
   ```bash
   lsof -i :8080   # for agent-svc
   lsof -i :8001   # for scraper-svc
   ```
5. **Review recent changes** — check if a recent deployment or configuration change may have introduced the failure.
6. **Verify Prometheus configuration** — ensure the prometheus scrape target for the service is correctly configured and the service is registered.

## Escalation Path

- **Level 1 (On-call engineer)**: Attempt service restart as described in Immediate Actions. If the service comes back successfully, monitor for 5 minutes to ensure stability.
- **Level 2 (Service owner)**: If the service fails to restart or repeatedly crashes within minutes of restarting, escalate to the service owner. Provide crash logs and any relevant error messages.
- **Level 3 (Infrastructure team)**: If the issue is caused by Docker host resource exhaustion, port conflicts, or infrastructure-level problems, escalate to the infrastructure team. If all services are affected, declare a major incident.
