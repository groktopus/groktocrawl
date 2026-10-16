# Deployment host Docker Infrastructure Check

**Date:** 2026-07-03
**Host:** deployment.example.internal (confirmed via `hostname`)

---

## 1. Docker Status

✅ **Docker is fully operational** on deployment.example.internal. `docker ps` returned a large list of running containers (30+).

## 2. Docker Compose

✅ **Docker Compose v5.1.3** is installed and available.

## 3. System Resources

| Resource | Value |
|----------|-------|
| RAM      | 14 GB (total), ~4.4 GB used, ~10 GB available |
| CPU      | 8 cores |
| Swap     | 975 MB (713 MB used) |
| OS       | Linux (not macOS — `sysctl` unavailable; `free`/`ss` available) |

## 4. Groktocrawl Project

### Project Location

The groktocrawl Docker Compose project was started from a **GitHub Actions runner workspace**:
- **Compose config path:** `/runner/work/groktocrawl/groktocrawl/docker-compose.yml`
- **Working directory:** `/runner/work/groktocrawl/groktocrawl`

⚠️ **The source directory no longer exists on deployment.example.internal.** The path `/runner/work/groktocrawl/groktocrawl/` now only contains an `ofelia/` subdirectory — the compose file and project source have been cleaned up. However, the **containers are still running** because they were started with `docker compose` and haven't been stopped.

⚠️ **The local development path `/path/to/groktocrawl` does not exist on deployment.example.internal.** This is a macOS path; deployment.example.internal is a Linux machine.

### Running Groktocrawl Services

Only **3 of the expected services** are currently running:

| Service | Container | Port | Status |
|---------|-----------|------|--------|
| test-site | groktocrawl-test-site-1 | 8005→8000 | Up 2 hours |
| tier3-fixture | groktocrawl-tier3-fixture-1 | 8006→8000 | Up 2 hours |
| llm-svc | groktocrawl-llm-svc-1 | 8011→8011 | Up 2 hours |

### Missing Services

These compose services are **not running**:
- `agent-svc` (main API + agent research loop)
- `search-svc` (search fixture)
- `scraper-svc` (URL → markdown service)
- `valkey` (job store / cache backend)

## 5. Key Ports in Use

| Port | Service | Notes |
|------|---------|-------|
| 22 | SSH | System SSH |
| 53 | DNS | Likely Pi-hole or similar |
| 80, 443 | Traefik | Reverse proxy (docker socket) |
| 8000 | Traefik | Traefik dashboard or API |
| 8005 | groktocrawl test-site | Test fixture website |
| 8006 | groktocrawl tier3-fixture | Tier 3 scraper fixture |
| 8010 | groktopus-www | groktop.us website |
| 8011 | groktocrawl llm-svc | LLM fixture |
| 8300 | Grafana | Monitoring dashboard |
| 8448 | Matrix/Synapse? | Likely Matrix federation |
| 8883 | MQTT? | Possibly Mosquitto |
| 9300-9305 | Python services | Miscellaneous python apps |
| 9417 | cAdvisor | Container monitoring |
| 11434 | Ollama | Local LLM inference (localhost only) |

## 6. Other Infrastructure Running on Deployment host

- **Traefik** (v3.7) — Reverse proxy handling 80/443
- **Grafana stack** — Grafana + Prometheus + Loki + Promtail for monitoring
- **GitHub Actions runners** — 5 instances (groktopus × 3, hermes-cashew, magnus919)
- **Forgejo runner** — For self-hosted git (forgejo)
- **Dokku** — Multiple apps (auth, time, note-qa, hello-fn)
- **Matomo** — Web analytics (with MariaDB backend)
- **Heimdall** — Dashboard/application portal
- **Watchtower** — Automatic container updates
- **cAdvisor** — Container resource monitoring
- **Ollama** — Local LLM serving (port 11434, localhost only)
- **magnus919.com** — Personal website (3 replicas)

## 7. Summary

Deployment host is a well-established Linux Docker host running diverse infrastructure including CI/CD runners (GitHub Actions, Forgejo), monitoring (Grafana/Prometheus), web analytics (Matomo), a Dokku PaaS, and local LLM inference (Ollama).

The groktocrawl project was deployed via a GitHub Actions CI pipeline. The compose project still exists in Docker but was started from a runner workspace that has since been cleaned up. Only the test fixtures and LLM service remain running — the core services (agent-svc, scraper-svc, search-svc, valkey) are not deployed.

To re-deploy groktocrawl on deployment.example.internal, you would need to:
1. Clone or rsync the project to a persistent location on deployment.example.internal (e.g., `/opt/groktocrawl` or `/home/operator/groktocrawl`)
2. Run `docker compose up -d` from that location to bring up all services
