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

Agent `search_type=deep` runs the planned queries and a bounded gap-focused
follow-up when needed. It keeps a minimum evidence floor derived from the
planned query count (three to five successful sources), while preserving
`max_credits`, cancellation, concurrency, and the maximum query budget. The
completed response includes a `coverage` object with planned/executed queries,
candidate and attempted URLs, successful sources, refusals, failures, reused
sources, pass count, the evidence floor, and any uncovered query dimensions.
`coverage_complete=false` means synthesis completed with those limitations
reported; it does not claim that three sources are comprehensive coverage.

The `/v2/agent` acquisition policy no longer accepts `max_results_per_query`; the old
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

### Experimental research protocol

The opt-in research protocol is separate from `/v2/agent` and `/v2/answer`:
`GET /experimental/research/v1/capabilities` reports enabled capabilities, and
`POST /experimental/research/v1/runs` starts a bounded run. Run status and events
are available under `/runs/{run_id}` and `/runs/{run_id}/events`; audited artifacts,
evidence, and explicit workspace actions have routes under the same prefix. See the
[retained research workspace guide](research-workspace.md) for named actions and
state boundaries.

The protocol uses `FEATURE_EXPERIMENTAL_RESEARCH`; run creation, durable execution,
and PostgreSQL artifact authority have separate feature flags during rollout. A
disabled capability returns 404. PostgreSQL is included in the standard Compose
deployment; durable operation requires its configured connection and explicit ordered
schema migrations. Existing inline jobs, sessions, research-memory records, and `/v2/*`
contracts are not imported or changed by enabling the protocol. The route name remains
`experimental` for compatibility and API stability while its capabilities mature; it
does not require a separate deployment or repository.

## Evidence selection and search continuation

`POST /v2/answer` accepts `evidence_budget_chars` (256–128000; default 32000). Its JSON response and SSE `done` event include `evidence_coverage`: method, budget_chars, selected_chars, complete, and sources with id/url, content_sha256, source_chars, selected_chars, omitted_chars and start/end spans. Offsets count Unicode characters into the full Markdown whose UTF-8 SHA-256 is reported. The budget counts source text; context headers are additional. `complete` means all supplied text was selected, not that an answer or discovery is exhaustive.

Research-agent completion `coverage.evidence` exposes the same default budget. `coverage.discovery_target_met` replaces the misleading coverage_complete goal claim; coverage_complete is false for bounded web discovery. Full source bodies stay in request artifacts and can be exported with include_source_content; research-memory caches preserve coverage but do not archive source bodies. Older cached artifacts report evidence.status=not_retained. Sessions retain full refs until their existing TTL/deletion boundary. Session query accepts optional ref_ids (must belong to that session) and evidence_budget_chars; query/deepen return evidence_coverage. Resolve refs to read complete content. No shared document index is introduced.

Fast non-streaming keyword search accepts `limit` (1–1000), `page` (1–1000) and `offset` (0–100000, within that upstream page). `coverage` reports captured_results, returned_results, omitted_results, has_more_in_page and unknown upstream_has_more. If more captured results remain, submit the returned `continuation` object to `/v2/search`. After exhausting that page, callers may request coverage.next_upstream_page with offset=0; engine support and further availability are unknown. Each continuation repeats the upstream query, so ordering can change. No exhaustive or snapshot guarantee is made. Images, streaming and non-fast/non-keyword modes reject nondefault page/offset with 422.

For example: `{"query":"capacitor measurements","limit":20,"page":1,"offset":0}`. Narrow a session question with `{"question":"Amber voltage","ref_ids":["ref_1_2"],"evidence_budget_chars":64000}`. Lexical selection scans all retained content but may miss synonyms; use coverage and complete refs when auditing omissions.


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
[X ADR-0095](../adr/experimental/groktocrawl-x/0095-retain-bounded-source-media-metadata.md) for bounds.

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
