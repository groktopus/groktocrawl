# W9 current-runtime checkpoint 0

- Runtime revision: `070b9e9e1a6b94c079b69ddd758dbc62223ae39d`
- Observation window start: `2026-09-20T05:16:52.26916171Z`
- Checkpoint completed: `2026-09-20T05:23:26.651758Z`
- Result: **passed, 12 of 12 declared operations**

The guarded W9 runner exercised the inherited scrape, crawl, search, answer,
agent, webhook, CLI, and MCP journeys plus the experimental cross-client
research verification against the exact deployed revision. PostgreSQL remained
the artifact authority, pgvector remained the serving vector store, the Qdrant
rollback copy remained available, and all eleven candidate services were
healthy.

The semantic service had the deployment's durable 14-CPU quota. Its resource
snapshot showed 1.166 GiB of 4 GiB memory in use after the checkpoint. This is a
point-in-time bounded observation, not a capacity claim.

The first invocation stopped before any product operation because the host
system Python lacked `httpx`. The retained [setup failure](setup-failure.json)
records that observation-tool error. Running the same checked-in tool through an
isolated Python environment with `httpx` completed the checkpoint. No failed
product request was discarded or retried into a pass.

The packet contains only loopback URLs, generic Compose service identities,
digests, timings, and bounded resource statistics. It contains no credentials,
private hostnames, private addresses, or personal filesystem paths.
