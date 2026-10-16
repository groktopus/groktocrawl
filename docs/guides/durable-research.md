# Live durable research

`POST /experimental/research/v1/runs` uses the server-configured search, scraper and OpenAI-compatible model. Enable the four research flags with isolated Valkey and PostgreSQL artifact authority schema 16; keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false`. Setting the fixture flag true selects a scripted development demonstration, which does not accept live time evaluation or callbacks.

```bash
curl "$GROKTOCRAWL_URL/experimental/research/v1/runs" \
  -H "Authorization: Bearer $GROKTOCRAWL_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: research-example-001' \
  -d '{"objective":"What does the published heat-pump trial establish, and what are its limitations?","webhook":"https://receiver.example/research"}'
```

Persist the idempotency key before sending. Retrying the same scoped request returns the same admission; changed input with that key returns 409. Poll the returned `status_url`. A completed result points to the checked knowledge, exact retained sources and three report layers. Known capture failures can yield insufficient coverage. Invalid construction or failed verification is a failed run, not a successful research result.

The policy permits at most three model-planned queries, eight total sources, 256,000 source bytes, 64 model calls and 180 seconds from admission. Selected retained evidence consumes the same source/byte budget and retains its original provenance. This is bounded research, not an unlimited search guarantee.

For direct HTTP run creation, an optional `as_of`, such as `"2026-09-01T00:00:00Z"`, evaluates evidence at that target time. It must include a timezone and cannot be in the future. It does not select a historical search index or recover archived pages. A newly captured page does not prove what was true before its capture; a run lacking eligible evidence fails with `historical_validity_unestablished`. Unknown retrieval dates remain unknown. Historical evidence must still pass support and other verification checks.

CLI `research create` and MCP `research_create` currently accept the objective,
idempotency key and optional webhook; use HTTP to set `as_of` at creation.

External call intents and exact completed responses are retained before dependent work starts. Restart reuses completed responses. An interrupted dispatch with no saved response fails as `outcome_unknown`; automatic replay could spend again and is prohibited. Cancellation fences later operations and publication, while an already sent external request cannot be recalled. A PostgreSQL result committed before interruption is reconciled without another research call.

The optional webhook receives `research.completed`, `research.failed` or `research.cancelled` with `id` and `webhookId` equal to the run UUID, plus `data.run_id`, `data.research_id`, `data.state` and the result receipt or error. The outbox revalidates destination safety for every attempt, signs the exact body using the configured webhook secret, and never follows redirects. Deduplicate by `webhookId`: an acknowledgement lost after your receiver accepted a request can cause a repeat. Three bounded attempts lead to `delivered`, `rejected` or `unconfirmed`. The status response exposes only notification state and attempt count, not the destination or payload. Outbox recovery lasts within the execution ledger retention advertised by capabilities; it does not promise delivery after that retention expires.

The verification model evaluates support; passing checks are not proof of factual truth or a human review. Read the sources and coverage qualification before relying on a report. API and integration tests use controlled local HTTP twins with real isolated retained stores and do not contact public search or paid providers.
