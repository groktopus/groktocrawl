# API guide

The API is served by `agent-svc` on port 8080. FastAPI publishes the authoritative schema at `/openapi.json`; use `/docs` for an interactive reference. The validated [public surface inventory](../reference/public-surface.md) lists every current route.

## Authentication and errors

Authentication is optional for local development and required for production. Set `API_KEY`, then send either `Authorization: Bearer <key>` or `X-API-Key: <key>`. `/health` and `/metrics` remain unauthenticated for infrastructure probes.

Errors use a common object with `error`, `error_code`, and optional `details`. Typical codes are `INVALID_REQUEST`, `AUTH_ERROR`, `NOT_FOUND`, `RATE_LIMITED`, `UPSTREAM_ERROR`, and `INTERNAL_ERROR`.

```bash
curl -X POST http://localhost:8080/v2/scrape \
  -H 'Authorization: Bearer YOUR_KEY' -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'
```

## Jobs, streaming, and webhooks

Scrape, map, search, answer, browser, and similar lightweight operations return directly. Crawl, extraction, batch scrape, and llms.txt generation create persistent job records: create the job, poll its status route, and cancel where a DELETE route is available. Job responses include IDs suitable for polling.

Persistent state is not restart-safe execution. Valkey preserves job status and completed results, but `agent-svc` executes work in-process. If that process exits before a job finishes, the job is not resumed or reclaimed automatically and may remain `processing` until its record expires. Cancellation can update the stored status, but it does not recover interrupted work. Partial writes to downstream stores are not rolled back, and completion or failure webhooks are not replayed after restart. Restart-safe execution is deferred until there is an explicit product requirement for a durable job owner, retry and lease semantics, cancellation behavior, artifact consistency, and idempotent webhook delivery.

`POST /v2/agent` and `POST /v2/answer` support SSE when `stream: true`; agent events include planning, source discovery, scraping, tokens, and completion. Crawls stream through `GET /v2/crawl/{job_id}/stream`, including replay for completed jobs. Consume each SSE event as JSON and treat `done`/`error` as terminal.

Every asynchronous creation request accepts webhook configuration. Completion and failure delivery is best effort and is not persisted for retry after process loss; verify the endpoint’s OpenAPI model for the exact field shape and sign requests with `WEBHOOK_SECRET` where configured.

## Common workflows

### Research or answer

Use `/v2/answer` for one grounded response with citations. Use `/v2/agent` for multi-query research, seed URLs, structured output, citation styling, image collection, plan events, and optional streaming. `search_type` selects the research depth where supported.

### Search and retrieval

`/v2/search` supports source/category filters, content extraction, optional streaming, structured extraction, and keyword/semantic/hybrid retrieval modes. Semantic modes depend on `semantic-svc` and Qdrant; keyword search depends on SlopSearX and its configured search provider.

### Agent-native state

The plan endpoints create and retrieve a consentable research plan; execution starts an approved plan. Sessions preserve stepwise research context. Research-memory endpoints query, store, batch, delete, and sweep reusable artifacts. Citation resolution expands compact citations. These APIs are public but are intentionally not all exposed by the CLI yet.

### Files and browser sessions

Use the two-step parse flow when an upload must be staged: `PUT /v2/parse/upload/{upload_id}`, then `POST /v2/parse` referencing that ID. Browser routes create, execute against, list, and destroy short-lived Playwright sessions.

## Compatibility

GroktoCrawl targets Firecrawl v2 request/response conventions for its compatible operations. GroktoCrawl-specific facilities—plans, sessions, research memory, citation resolution, enrichment, semantic similarity, portal support, and MCP—extend that surface. Do not infer unsupported Firecrawl options from compatibility language; consult `/openapi.json` for accepted fields.
