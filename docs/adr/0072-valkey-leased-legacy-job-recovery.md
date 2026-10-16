# ADR-0072: Recover Legacy Async Jobs with Valkey Leases

**Status:** Accepted
**Date:** 2026-10-15

## Context

ADR-0047 documented that legacy asynchronous endpoints ran inside the API
process and left `processing` records stranded after a restart. Replaying every
interrupted worker is unsafe: an external search, scrape, model call, or
downstream write may have succeeded even when the process died before recording
its response. A blind retry can duplicate paid or non-idempotent work.

The service already uses Valkey for job records. The supported asynchronous
families are crawl, agent, extract, batch scrape, llmstxt, and plan execution.
Some routes also expose streaming behavior; it shares job ownership and must
not be mistaken for a resumable durable stream.

## Decision

Keep the existing in-process `TaskTracker`; do not add a broker. New jobs carry
an internal versioned execution marker. Before a worker starts, it atomically
claims the job with a Valkey lease and monotonically increasing fence. A fresh
process may reconstruct and dispatch a job only while its marker proves it is
still queued and unclaimed. The persisted route payload must contain all
normalized inputs needed by that worker; recovery does not repeat NL planning.

Terminal completion and failure writes verify the live owner and fence. A
delete marks the job cancelled and revokes the fence before cancelling its
local task. A lost lease prevents the old worker from publishing terminal
state. Progress and external side effects are not transactional across Valkey,
the scraper, the search service, the model provider, or the semantic index.

If a worker had claimed a job when its process disappears, recovery does not
invoke it again. Once its lease expires, the job becomes `failed` with
`error_code=OUTCOME_UNKNOWN`; the operator or caller must decide whether to
submit a new job. Records created before the execution marker existed follow
the same fail-closed path. This is truthful recovery, not automatic continuation
or exactly-once execution.

Webhook events are persisted in a per-job Valkey outbox. Terminal worker state
and its completion/failure webhook intent are written in the same fenced Lua
transition. Outbox records use the existing UUIDv4 `webhookId`, deduplicate
repeated event intents by their canonical event fingerprint, and have a lease
so only one process sends a record at a time. After restart, pending or expired
delivery leases are retried with the same body and ID. Delivery is at-least-once:
if the receiver accepted an event but the response was lost, the receiver must
deduplicate by `webhookId`. Invalid destinations are abandoned under the
existing outbound-webhook policy. Events without a configured webhook are not
stored.

The six families share the same queued-only recovery rule. The recovery tests
exercise the current worker signatures with test doubles and verify Valkey
ownership, fresh-process redispatch, stale-job failure, and outbox replay.
They do not claim that provider calls, downstream artifacts, or multi-service
effects resume transactionally after interruption.

Synchronous plan execution atomically deletes the one-shot plan while creating
its queued job record. This closes the crash window where a plan could be
consumed before any recoverable job existed. Streaming plan execution remains
inline and does not use that background-job recovery contract.

## Consequences

- Jobs accepted but not yet claimed can survive an API process restart.
- Claimed work is never blindly replayed; it terminates as
  `OUTCOME_UNKNOWN` after lease expiry.
- Terminal webhook intent survives a crash between state publication and
  delivery. Receivers need idempotency handling for duplicate delivery.
- Partial crawl pages, semantic-index writes, or research-memory writes are
  retained as observed; this ADR does not add cross-service rollback.
- The API status surface remains compatible; the internal execution marker and
  webhook outbox are implementation details.
- Existing pre-marker jobs may become explicitly failed after deployment.
  Operators should inspect the failure code and submit critical work again
  only after checking for external effects.

## Links

- [ADR-0047: Defer Restart-Safe Execution](0047-defer-restart-safe-execution.md)
- [Interrupted Jobs Runbook](../runbooks/interrupted-jobs.md)
