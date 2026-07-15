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

## Verification

1. Validate the deployment's Prometheus configuration and rules with its native check commands.
2. Confirm all three targets are healthy and query a current metric, for example `up{job="agent-svc"}`.
3. Import or provision the dashboards and confirm their UIDs are loaded.
4. Confirm the alert rules are loaded and test the deployment contact point.
5. Run the supported external API probe:

   ```bash
   groktocrawl --server "$GROKTOCRAWL_API_URL" --json search "observability probe" --limit 1
   ```
