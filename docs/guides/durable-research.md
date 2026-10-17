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

New live admissions use policy `durable-live/5`: at most three model-planned queries, eight total sources, 256,000 source bytes, 64 model calls and five minutes from admission. Selected retained evidence consumes the same source/byte budget and retains its original provenance. Planning, capture, drafting and review share that five-minute allowance. This is a finite execution limit, not a speed target or a guarantee of a complete answer. Earlier admissions retain their original overall and stage limits. There are no automatic model retries. See [ADR-0074](../adr/0074-use-a-five-minute-live-research-deadline.md) for the measured reason and rollback requirements.

For direct HTTP run creation, an optional `as_of`, such as `"2026-09-01T00:00:00Z"`, evaluates evidence at that target time. It must include a timezone and cannot be in the future. It does not select a historical search index or recover archived pages. A newly captured page does not prove what was true before its capture; a run lacking eligible evidence fails with `historical_validity_unestablished`. Unknown retrieval dates remain unknown. Historical evidence must still pass support and other verification checks.

CLI `research create` and MCP `research_create` currently accept the objective,
idempotency key and optional webhook; use HTTP to set `as_of` at creation.

External call intents and exact completed responses are retained before dependent work starts. Restart reuses completed responses. An interrupted dispatch with no saved response fails as `outcome_unknown`; automatic replay could spend again and is prohibited. Cancellation fences later operations and publication, while an already sent external request cannot be recalled. A PostgreSQL result committed before interruption is reconciled without another research call.

The optional webhook receives `research.completed`, `research.failed` or `research.cancelled` with `id` and `webhookId` equal to the run UUID, plus `data.run_id`, `data.research_id`, `data.state` and the result receipt or error. The outbox revalidates destination safety for every attempt, signs the exact body using the configured webhook secret, and never follows redirects. Deduplicate by `webhookId`: an acknowledgement lost after your receiver accepted a request can cause a repeat. Three bounded attempts lead to `delivered`, `rejected` or `unconfirmed`. The status response exposes only notification state and attempt count, not the destination or payload. Outbox recovery lasts within the execution ledger retention advertised by capabilities; it does not promise delivery after that retention expires.

The verification model evaluates support; passing checks are not proof of factual truth or a human review. Read the sources and coverage qualification before relying on a report. API and integration tests use controlled local HTTP twins with real isolated retained stores and do not contact public search or paid providers.


## When evidence cannot be verified

A model can find supporting text without establishing that it is current. A recent
capture alone does not establish when a page was published or took effect. The
publication checks keep that distinction.

Reports omit additional conclusions that do not pass verification and say how
many were omitted. If a conclusion needed to answer the question cannot pass,
the run ends as `failed` with code `evidence_not_verified`, a readable `message`,
and reason `source_freshness_unverified` or `answer_support_unverified`. It does
not publish an unverified answer. A request for a past date still uses
`historical_validity_unestablished` when the available evidence cannot establish
that historical answer.

These are final outcomes for that admission. Reading status or recovering the
run preserves its completed search and model responses; it does not repeat those
calls. A clearer error is not evidence that the original question was answered.


## What a new report establishes

New research describes what its captured sources say. Each report makes that
scope explicit. A page does not need a publication date for the system to report
what it said when captured. This does not certify that the page's assertions are
still true today. Questions that need stronger current evidence must keep that
limitation visible rather than claiming a complete answer.

The source text, capture dates, citations, and verification results are retained.
Missing capture dates, unsupported statements, and captures made after a requested
past date still cannot pass the existing checks. Earlier admissions keep their
original rules and saved results. See [ADR-0073](../adr/0073-publish-source-bounded-research.md).

## When a model reaches its output limit

A received `length` finish reason stops the run with `model_output_limit` and
`retryable: false`. No partial report is published. Status includes only safe
HTTP status, finish reason, and valid token counts. The service retains a failure
receipt so a restart does not make the same model call again. You can submit a
separate, narrower question if useful.

A timeout or disconnected call remains `outcome_unknown`; it is not automatically
repeated. An exact token count at the ceiling alone does not prove that an older
failed call reached its output limit. See [ADR-0075](../adr/0075-retain-known-model-output-limit-receipts.md).
