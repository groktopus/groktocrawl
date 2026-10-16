# W9 operational pilot checkpoint 0

Status: **passed**

The start-of-window checkpoint completed all 11 inherited compatibility journeys
and one experimental research run. Scrape, crawl, search, answer, agent, SSE,
webhook, CLI, and MCP paths completed. The experimental research run completed
and returned the same retained artifact set through HTTP, SSE, CLI, and MCP.

The candidate reported PostgreSQL schema 14, pgvector serving, a healthy Qdrant
rollback copy, and all ten services healthy. This checkpoint contributes 12
successful representative requests toward the minimum of 30.

The first invocation used the host Python rather than the checkout's existing
virtual environment and stopped before making a product request because `httpx`
was unavailable. It is recorded here as an observation-tool setup error and does
not count as a request, product failure, or retry.

The next checkpoint cannot count before `2026-09-14T17:26:19Z`. The pilot cannot
complete before `2026-09-18T17:26:19Z`, even if the request minimum is reached
sooner.
