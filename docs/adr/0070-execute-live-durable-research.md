# Execute live durable research with persisted external-call receipts

- Status: proposed
- Date: 2026-10-16
- Deciders: GroktoCrawl maintainers
- Scope: `/experimental/research/v1` run execution; legacy jobs are unchanged

## Context

ADR-0069 adopted storage and verification components, but the HTTP run worker selected a scripted example. Separately tested real-source construction did not prove that an HTTP objective acquired sources or retained those bytes. Connecting that function without lifecycle integration would repeat external calls after crashes: the old worker renewed no lease and checkpointed only its terminal result.

## Decision

Use the existing server-configured SlopSearX client, scraper client, and OpenAI-compatible review transport. Model/source text cannot select endpoints, credentials, models, or budgets. Four existing research flags enable the live durable path only with Valkey and PostgreSQL artifact authority schema16. The development-only fixture flag explicitly selects fictional execution. Historical admissions without an execution mode remain fixture admissions; recovery never silently upgrades them to live work.

Admission fixes the scoped run/research IDs, configured model and token-budget policy, and evaluation clock. Construction, relationship and check identities are reproducible from admitted identities. Selected retained material is internal, scoped server context, included in idempotency and combined source budgets.

Before each external operation, atomically persist its identity, input digest and dispatch intent under the current Valkey fencing generation. Persist its bounded exact output before starting dependent work. Recovery reuses completed responses. An intent without a receipt is ambiguous and produces the persistent nonretryable `outcome_unknown` failure; it never authorizes an automatic repeated paid call. This is not an exactly-once network guarantee. New explicit requests remain separate admissions.

Renew the worker lease during execution. A short PostgreSQL advisory transaction lock keyed by scope/research serializes ownership claims, publication and cancellation; deletion uses the same key. No search/acquisition/model calls occur inside this lock. Within publication, check Valkey ownership, then atomically retain the complete report, knowledge and evidence set. Reconciliation projects an already committed PostgreSQL root after an interrupted Valkey terminal update without invoking adapters again. If authority is unavailable, do not invent an uncommitted failure for a potentially committed result. Cancellation records its fence before cancelling a local task; another process's stale worker cannot begin a subsequent ledger operation or publish. A committed root wins over a later cancellation.

Startup scans reclaimable admissions and tracks worker shutdown. Terminal failures and cancellation survive process loss; process-local status is synchronized from the durable ledger. Errors are redacted stable codes, not source/provider content or credentials.

## Current bounds and remaining release work

The initial adapter policy is one search, at most three fresh sources, 256000 source bytes and an absolute 180-second execution deadline. Up to eight selected retained sources can supplement the plan within the same byte budget; fresh slots shrink to keep eight sources total. Unknown original URLs and retrieval dates remain null rather than fabricated. Construction and review calls have bounded count and output. These bounds are not equivalent to the existing iterative deep-research loop.

`as_of` and `webhook` are explicitly rejected before admission in this initial core change. Their semantics, broader bounded discovery, executable workspace follow-ups and source handoff remain required release work tracked by issue #666. This ADR is not evidence that the complete 1.0.1 goal has shipped.

## Confirmation

Require objective-dependent HTTP search/capture/model tests against controlled local services and actual isolated Valkey/PostgreSQL, retained source/report integrity across a fresh application, scoped idempotency, completed-step reuse, refusal to replay ambiguous dispatch, persistent failure, renewed ownership and competing cancellation. Inject interruptions after external receipts and PostgreSQL commit. Existing fixture-only API tests cannot substitute for these checks. No provider calls or production deployment are required to run acceptance.

## Consequences

An ambiguous external dispatch can stop a run that may have incurred cost; this is deliberate and visible, rather than silently spending again. PostgreSQL and Valkey availability are both required for live admission. Recovery does not guarantee that lost Valkey operation receipts can be reconstructed from provider billing. Existing retained PostgreSQL readers and legacy fixture records remain compatible; no historical evidence is rewritten.

## Links

- [ADR-0069](0069-adopt-bounded-research-substrate.md)
- [Completion issue #666](https://github.com/groktopus/groktocrawl/issues/666)
