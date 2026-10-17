# Execute live durable research with persisted external-call receipts

- Status: accepted
- Date: 2026-10-16
- Deciders: GroktoCrawl maintainers
- Scope: `/experimental/research/v1` run execution; legacy jobs are unchanged

Later changes: [ADR-0073](0073-publish-source-bounded-research.md) defines source-bound answers. [ADR-0074](0074-use-a-five-minute-live-research-deadline.md) gives new version-5 admissions a five-minute deadline; earlier policies keep their original limits.

[ADR-0075](0075-retain-known-model-output-limit-receipts.md) adds sanitized receipts for positively received model output-limit failures. Unknown dispatches retain the no-replay contract.

## Context

ADR-0069 adopted storage and verification components, but the HTTP run worker selected a scripted example. Separately tested real-source construction did not prove that an HTTP objective acquired sources or retained those bytes. Connecting that function without lifecycle integration would repeat external calls after crashes: the old worker renewed no lease and checkpointed only its terminal result.

## Decision

Use the existing server-configured SlopSearX client, scraper client, and OpenAI-compatible review transport. Model/source text cannot select endpoints, credentials, models, or budgets. Four existing research flags enable the live durable path only with Valkey and PostgreSQL artifact authority schema16. The development-only fixture flag explicitly selects fictional execution. Historical admissions without an execution mode remain fixture admissions; recovery never silently upgrades them to live work.

Admission fixes the scoped run/research IDs, configured model and token-budget policy, and evaluation clock. Construction, relationship and check identities are reproducible from admitted identities. Selected retained material is internal, scoped server context, included in idempotency and combined source budgets.

Before each external operation, atomically persist its identity, input digest and dispatch intent under the current Valkey fencing generation. Persist its bounded exact output before starting dependent work. Recovery reuses completed responses. An intent without a receipt is ambiguous and produces the persistent nonretryable `outcome_unknown` failure; it never authorizes an automatic repeated paid call. This is not an exactly-once network guarantee. New explicit requests remain separate admissions.

Renew the worker lease during execution. A short PostgreSQL advisory transaction lock keyed by scope/research serializes ownership claims, publication and cancellation; deletion uses the same key. No search/acquisition/model calls occur inside this lock. Within publication, check Valkey ownership, then atomically retain the complete report, knowledge and evidence set. Reconciliation projects an already committed PostgreSQL root after an interrupted Valkey terminal update without invoking adapters again. If authority is unavailable, do not invent an uncommitted failure for a potentially committed result. Cancellation records its fence before cancelling a local task; another process's stale worker cannot begin a subsequent ledger operation or publish. A committed root wins over a later cancellation.

Startup scans reclaimable admissions and tracks worker shutdown. Terminal failures and cancellation survive process loss; process-local status is synchronized from the durable ledger. Errors are redacted stable codes, not source/provider content or credentials.

## Bounded discovery, time and callbacks

New admissions pin policy `durable-live/3`: one recorded model planning call produces at most three distinct queries, followed by round-robin source selection across their results. At most eight sources, 256000 source bytes, 64 model calls and an absolute 180-second execution deadline are permitted. Selected retained sources consume the same budgets; eight retained sources need no new search. A known failed capture can leave a checked report with insufficient coverage, never an unqualified complete result. Ambiguous dispatch still stops immediately. Older admissions keep their one-search/three-fresh-source policy so restart does not alter retained operation identities. These bounds are not equivalent to the existing iterative deep-research loop.

Policy version 3 also uses the admitted completion allowance, capped at 8192 tokens, for planning, construction and review. A production smoke test showed that the former 2048-token construction ceiling could be consumed entirely by model reasoning, leaving no answer. Raising that ceiling alone did not resolve the observed latency. Construction therefore selects relevant whole lines within a shared 32000-character allowance, divided across sources. Full captured source text remains retained. Original line numbers are preserved, and a model citation that crosses omitted lines is rejected. The prompt explicitly warns that excerpts cannot establish exhaustive coverage. These are request limits, not performance targets; measured completion times remain observations.

Admissions under `durable-live/2` keep their original full-source construction and 2048-token request ceiling. Their recorded request identities and receipts must remain replayable. Version 3 changes only newly admitted work and does not authorize retries of ambiguous external calls.

`as_of` means a timezone-aware, nonfuture evidence evaluation target. It does not request historical search or fabricate archived captures. Original URLs and retrieval dates remain null when unknown. Freshness checks compare actual retained evidence times with that target; unsupported past validity fails as `historical_validity_unestablished`, even if a model returns a passing opinion. Evidence captured before the target remains eligible for the other checks.

Validate an optional webhook URL before admission using the existing outbound destination policy, then persist it with the admitted payload. The terminal record and notification index are created without a publish-to-enqueue gap. A separate startup worker claims each terminal notification under a bounded lease and persists one exact event body and stable UUID before sending. Completion, failure and cancellation are distinct events. Up to three attempts reuse that body and ID, revalidate the destination, sign the exact bytes, and never follow redirects. Permanent destination/HTTP rejection is `rejected`; exhausting retries or losing the final acknowledgement is `unconfirmed`, not delivered. Receiver deduplication is required: HTTP delivery is at least once, not exactly once. Deletion fences future attempts; an already dispatched callback cannot be recalled. Callback retries never rerun research or paid calls.

Executable workspace follow-ups and source handoff are integrated separately and must pass combined HTTP/retained-store acceptance before issue #666 or the 1.0.1 goal can close.

## Confirmation

Require objective-dependent HTTP search/capture/model tests against controlled local services and actual isolated Valkey/PostgreSQL, retained source/report integrity across a fresh application, scoped idempotency, completed-step reuse, refusal to replay ambiguous dispatch, persistent failure, renewed ownership and competing cancellation. Inject interruptions after external receipts and PostgreSQL commit. Existing fixture-only API tests cannot substitute for these checks. No provider calls or production deployment are required to run acceptance.

## Consequences

An ambiguous external dispatch can stop a run that may have incurred cost; this is deliberate and visible, rather than silently spending again. PostgreSQL and Valkey availability are both required for live admission. Recovery does not guarantee that lost Valkey operation receipts can be reconstructed from provider billing. Existing retained PostgreSQL readers and legacy fixture records remain compatible; no historical evidence is rewritten.

## Links

- [ADR-0069](0069-adopt-bounded-research-substrate.md)
- [Completion issue #666](https://github.com/groktopus/groktocrawl/issues/666)
