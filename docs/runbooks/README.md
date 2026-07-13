# GroktoCrawl Runbooks

Operational runbooks for common incidents and maintenance procedures.

Owner: GroktoCrawl maintainers

## Alert Runbooks

- [HighJobErrorRate](high-job-error-rate.md)
- [QueueDepthSpike](queue-depth-spike.md)
- [ServiceDown](service-down.md)

---

## Service Health Check

```bash
# Check all services
docker compose ps

# Per-service health endpoints
curl -s http://localhost:8080/health   # agent-svc
curl -s http://localhost:8001/health   # scraper-svc
curl -s http://localhost:8003/health   # semantic-svc
curl -s http://localhost:8082/health   # portal-svc
```

## Restart a Service

```bash
docker compose up -d --force-recreate <service-name>
# Example:
docker compose up -d --force-recreate scraper-svc
```

## Valkey (Redis) Recovery

If Valkey is unavailable or the container will not start:

```bash
# Inspect the failure, restart or recreate the service, then verify it responds.
docker compose logs --tail=200 valkey
docker compose up -d --force-recreate valkey
docker compose exec valkey valkey-cli PING
```

## Qdrant Recovery

```bash
# Check Qdrant health
curl -s http://localhost:6333/health

# Restart Qdrant
docker compose restart qdrant

# If Qdrant OOMs (common with large indexes):
# The docker-compose.yml already sets mem_limit: 4g.
# If needed, increase: docker compose up -d --force-recreate qdrant
```

## Logs

```bash
# View logs for a specific service
docker compose logs -f agent-svc

# View recent errors across all services
docker compose logs --tail=200 | grep -i error
```

## Common Error Patterns

### "Playwright browser probe failed"
The scraper-svc couldn't start Chromium. Check:
- Docker host has enough memory (Chromium needs ~500MB)
- `--no-sandbox` is in the launch args (Dockerfiles handle this)

### "Connection refused" to Valkey
Valkey isn't ready yet. Wait for the health check to pass:
```bash
docker compose ps valkey
```

### "Qdrant OOM" / semantic-svc 503
Qdrant ran out of memory. Restart with higher memory limit or reduce index size.

## Monitoring

- agent-svc metrics: `curl http://localhost:8080/metrics`
- semantic-svc metrics: `curl http://localhost:8003/metrics`
- Grafana dashboard: `docs/grafana/semantic-svc-dashboard.json`
