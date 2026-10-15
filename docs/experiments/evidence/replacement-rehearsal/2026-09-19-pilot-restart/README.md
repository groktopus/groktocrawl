# W9 operational pilot restart and checkpoint 0

Status: **restart checkpoint passed after retained operational failure**

The original W9 window did not survive a host restart. The candidate's private
environment and PostgreSQL password bind source had been stored under volatile
temporary storage. The containers stopped on 2026-09-16, and Compose could not
recreate them after those host files disappeared. This is failed operational
evidence. It is not erased or reclassified as a successful pilot interval.

The same private configuration was restored under persistent per-user storage
with owner-only directory and file permissions. No candidate source or runtime
image changed. All ten services returned healthy, PostgreSQL remained at
research schema 14, pgvector remained the serving vector store, and Qdrant
reported rollback ready.

At `2026-09-19T02:19:40Z`, the restarted checkpoint completed all 11 inherited
compatibility journeys plus one experimental cross-client research journey.
Scrape, crawl, search, answer, agent, SSE, webhook, CLI, and MCP paths completed.
The research verifier confirmed matching retained artifacts through HTTP, CLI,
and MCP. This contributes 12 successful representative operations.

The first checkpoint invocation copied a shell-quoted API key literally from the
private environment and received HTTP 403 before progressing beyond scrape. The
failed observation attempt is retained as `compatibility-setup-error.json`; it
is an observation-command error rather than a product request failure. The
runbook now obtains the effective key from Compose, which correctly removes env
file quoting.

Checkpoint 1 cannot count before `2026-09-22T02:19:40Z`. The pilot cannot pass
before checkpoint 2 at or after `2026-09-26T02:19:40Z`, even if the request
minimum is reached earlier.
