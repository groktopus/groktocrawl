# API guide

The API is served by `agent-svc` on port 8080. FastAPI publishes the authoritative schema at `/openapi.json`; use `/docs` for an interactive reference. The validated [public surface inventory](../reference/public-surface.md) lists every current route.

## Authentication and errors

Authentication is optional for local development and required for production. Set `API_KEY`, then send either `Authorization: Bearer <key>` or `X-API-Key: <key>`. `/health` and `/metrics` remain unauthenticated for infrastructure probes.

If both key headers are sent, a valid Bearer key takes precedence; otherwise a valid `X-API-Key` is used. Sessions and per-user research memory use that same key for their scope. `RESEARCH_MEMORY_SCOPE=global` still shares cached research. Without authentication, supplied key headers are cache-partition hints and do not establish caller identity.

Errors use a common object with `error`, `error_code`, and optional `details`. Typical codes are `INVALID_REQUEST`, `AUTH_ERROR`, `NOT_FOUND`, `RATE_LIMITED`, `UPSTREAM_ERROR`, and `INTERNAL_ERROR`.

```bash
curl -X POST http://localhost:8080/v2/scrape \
  -H 'Authorization: Bearer YOUR_KEY' -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'
```

### Rate limits and retries

Per-client rate limits reject `POST /v2/agent`, `POST /v2/answer`, and `POST /v2/crawl` before a job is created. A rejection is HTTP 429 with `error_code: RATE_LIMITED`, `retryable: true`, a positive `retry_after_seconds`, and `details` describing the non-secret bucket (`search` or `crawl`), limit, remaining, and reset time. The response also carries standard `Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` headers. Because rejection happens before job creation, retrying a definitively rejected admission never creates duplicate jobs.

A 429 admission is a temporary condition, not an operation failure: the CLI (`answer`, `agent`, `crawl`) retries automatically with a bounded policy (at most 3 total attempts, server-provided delays clamped to 60s, fallback backoff with jitter; see [ADR-0053](../adr/0053-retryable-rate-limit-contract.md)). Retry progress goes to stderr; `--json` stdout stays valid JSON. Other clients can apply the same policy from the response metadata.

Accepted asynchronous jobs that hit a downstream rate-limit condition (for example SlopSearX answering 429) transition to the non-terminal `retry_scheduled` status, exposed via `retry_at`, `retry_attempt`, `retry_limit`, `retryable: true`, and `retry_reason: RATE_LIMITED` on the status response, with a `retry_scheduled` webhook event. The worker resumes the same job after the bounded delay; exhaustion fails the job with rate-limit details. A scheduled retry is in-process state: it is not resumed after a process restart (see the durability paragraph below and ADR-0047).

## Jobs, streaming, and webhooks

Scrape, map, search, answer, browser, and similar lightweight operations return directly. Crawl, extraction, batch scrape, and llms.txt generation create persistent job records: create the job, poll its status route, and cancel where a DELETE route is available. Job responses include IDs suitable for polling.

Persistent state is not restart-safe execution. Valkey preserves job status and completed results, but `agent-svc` executes work in-process. If that process exits before a job finishes, the job is not resumed or reclaimed automatically and may remain `processing` until its record expires. Cancellation can update the stored status, but it does not recover interrupted work. Partial writes to downstream stores are not rolled back, and completion or failure webhooks are not replayed after restart. Restart-safe execution is deferred until there is an explicit product requirement for a durable job owner, retry and lease semantics, cancellation behavior, artifact consistency, and idempotent webhook delivery.

Operators: the [deployment guide](deployment.md#job-durability-and-recovery) documents the durability contract and recovery procedure, the [Interrupted Jobs runbook](../runbooks/interrupted-jobs.md) covers identifying and reconciling jobs stranded in `processing`, and [ADR-0047](../adr/0047-defer-restart-safe-execution.md) records the decision and roadmap.

`POST /v2/agent` and `POST /v2/answer` support SSE when `stream: true`; agent events include planning, source discovery, scraping, tokens, and completion. Crawls stream through `GET /v2/crawl/{job_id}/stream`, including replay for completed jobs. Consume each SSE event as JSON and treat `done`/`error` as terminal.

Every asynchronous creation request accepts webhook configuration. Completion and failure delivery is best effort and is not persisted for retry after process loss; verify the endpoint’s OpenAPI model for the exact field shape and sign requests with `WEBHOOK_SECRET` where configured.

Webhook destinations are validated before delivery: only `http`/`https` URLs resolving to public hosts are accepted, and private, loopback, link-local, multicast, metadata, and other restricted destinations are skipped with a warning in the service log. Redirects are not followed during delivery, so a validated destination cannot be redirected to a restricted host. Monitor webhooks follow the same policy.

## Common workflows

### Research or answer

Use `/v2/answer` for one grounded response with citations. Use `/v2/agent` for multi-query research, seed URLs, structured output, citation styling, image collection, plan events, and optional streaming. `search_type` selects the research depth where supported.

The experimental agent no longer accepts `max_results_per_query`; the old
JSON field is rejected with HTTP 422 rather than ignored. The matching CLI
flag and MCP argument are also removed. Omit them and use `max_credits` only
when an explicit attempt budget is required. This does not change the
caller-provided `limit` on `POST /v2/search`.

### Search and retrieval

`/v2/search` supports source/category filters, content extraction, optional streaming, structured extraction, and keyword/semantic/hybrid retrieval modes. Semantic modes depend on `semantic-svc` and Qdrant; keyword search depends on SlopSearX and its configured search provider.

### Agent-native state

The plan endpoints create and retrieve a consentable research plan; execution starts an approved plan. Sessions preserve stepwise research context. Research-memory endpoints query, store, batch, delete, and sweep reusable artifacts. Citation resolution expands compact citations. These APIs are public but are intentionally not all exposed by the CLI yet.

### Files and browser sessions

Use the two-step parse flow when an upload must be staged: `PUT /v2/parse/upload/{upload_id}`, then `POST /v2/parse` referencing that ID. Browser routes create, execute against, list, and destroy short-lived Playwright sessions.

## Compatibility

GroktoCrawl targets Firecrawl v2 request/response conventions for its compatible operations. GroktoCrawl-specific facilities—plans, sessions, research memory, citation resolution, enrichment, semantic similarity, portal support, and MCP—extend that surface. Do not infer unsupported Firecrawl options from compatibility language; consult `/openapi.json` for accepted fields.


### Source-reported media metadata

Search results may include optional `media` records with `media_type` (`image`
or `video`), original `url`, `thumbnail`, source-page `source`, pixel `width`/
`height`, and `duration` in seconds. Values are retained only when reported by
the upstream engine; missing values stay absent. Existing engine attribution
and scholarly fields remain intact. API sync/SSE, CLI JSON, MCP and session refs
retain the same bounded record. Image search retains its existing Firecrawl
fields and adds this metadata. News does not acquire a fabricated media kind.

This is discovery metadata: previews/originals are not downloaded or used as
crawl evidence. Unsafe/private/local URLs, signed or credential-bearing links,
malformed values, unknown fields and embed HTML are omitted. A public hostname
is not a guarantee about DNS or permission to fetch/render it. See
[ADR-0095](../adr/0095-retain-bounded-source-media-metadata.md) for bounds.

### Bounded scholarly search

`POST /v1/search` and `POST /v2/search` accept optional inclusive publication
`date_from` and `date_to` strings in canonical `YYYY-MM-DD` format. One-sided
ranges are supported. Explicit `engines: ["openalex"]` is required. Exact dates
cannot be combined with `time_range`; relative windows are not supported on this
API. This audited slice requires non-streaming `fast` search with `keyword`
retrieval, optional `web` source and `science`/`research` category, and no content
or structured-output enrichment. Other combinations fail validation before work.

Successful bounded responses include `date_filter` with requested and applied
bounds, enforcement status, engine identity and warnings. Results retain
`publishedDate`. An old or incompatible backend, missing enforcement proof,
failed acquisition, or out-of-range result fails closed rather than returning an
unbounded success. Deploy the compatible search backend before this client.
This contract does not establish publication-date coverage for general news.
