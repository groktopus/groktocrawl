# Defer Restart-Safe Execution with an Explicit Job-Durability Contract

* Status: accepted
* Deciders: Magnus Hedemark, Jasper (AI Agent)
* Date: 2026-08-03

## Context and Problem Statement

Async work is launched in-process with `asyncio.create_task()` through `TaskTracker` (agent-svc) and similar lifecycle handling (semantic-svc). Job records persist in Valkey for 24 hours, but execution state, retry scheduling, and webhook delivery do not survive a process restart. After process loss: a job record may remain `processing` until TTL expiry; cancellation can update persisted state but cannot resume work; partial artifacts already written to Valkey, Qdrant, or other downstream systems can remain; and an undelivered completion or failure webhook is not replayed.

Issue #458 closed the documentation gap: `docs/architecture.md` and `docs/guides/api.md` now distinguish persisted job state from restart-safe execution, and ADR-0035 already provides a five-second graceful-shutdown window. What is still missing is an explicit, dated, operator-facing statement of the contract: the SLO boundary operators can rely on, the recovery procedure for jobs stranded in `processing`, and the recorded direction for durable execution so the decision does not have to be re-derived (issue #470). Without it, deployments implicitly assume durable background execution, and every restart turns into an unstructured manual reconciliation.

## Decision Drivers

1. **Single-node MVP scope.** GroktoCrawl targets `docker compose up` deployments ("minimal moving parts" in VISION.md). Adding a durable-execution stack today trades operational simplicity for a guarantee no current deployment has measured a need for.
2. **Operational honesty.** Public quickstart and deployment guidance must not imply durable background execution. The boundary must be stated with an SLO an operator can plan around.
3. **Recovery without new infrastructure.** Operators must be able to identify and reconcile jobs stranded in `processing` using the existing stack (Valkey, existing endpoints) — no new services.
4. **Zero new dependencies for this change.** The contract and tooling must use the existing `redis` client and Valkey only.
5. **Race safety.** Any reconciliation must not overwrite a genuinely live job: only a `processing` → `failed` transition is permitted, matching `JobStore.fail_job` semantics (ADR-0035 context).
6. **Future work must be testable.** When durable execution is implemented, each increment must be independently shippable and verifiable, and the choice of queue technology must follow the requirement, not precede it (issue #458).

## Considered Options

### Option A — Document the contract, ship recovery tooling, defer implementation (chosen)

Record the durability contract and SLO boundary in an accepted ADR, add an operator-facing deployment callout, add a runbook, and ship `scripts/reconcile-jobs.py` so operators can list and fail jobs stranded in `processing`.

**Pros:** zero new infrastructure; no API surface change (ADR-0039); operators get a concrete recovery path today; the decision becomes explicit and dated.
**Cons:** no completion guarantee across restarts remains; recovery is manual (operator-initiated).

### Option B — RQ worker queue now

Move background processing to an RQ queue with dedicated workers and acknowledged tasks.

**Pros:** production-grade durability; survives process crashes; scales horizontally.
**Cons:** adds infrastructure and a new deployment contract; RQ machinery was removed in #196; a queue alone does not define leases, retries, cancellation, artifact consistency, or idempotent webhook delivery — those still need an ADR and design before implementation. Superseded by Option E's incremental path.

### Option C — Temporal or another external orchestrator

**Pros:** turnkey durability, retries, and activity heartbeats.
**Cons:** a heavy external dependency with its own operational surface, contradicting VISION.md's minimal-moving-parts principle; disproportionate for a single-node MVP.

### Option D — LangGraph checkpointing

**Pros:** reuses the research agent's graph execution for state persistence.
**Cons:** a graph checkpoint persists graph state, not job execution semantics (leases, retries, cancellation, webhook delivery). It would still require the full durability machinery on top, and it couples job execution to a graph runtime (see issue #458 discussion #427: "whether a graph checkpoint is being mistaken for durable job execution").

### Option E — Valkey-native leases + webhook outbox (future target direction)

When implementation is triggered, build durability on the existing Valkey boundary: per-job execution leases with heartbeats and reclaim, a persisted retry policy, idempotency keys, and a webhook outbox that survives restart and replays undelivered events (deduplicated by the existing `webhookId`).

**Pros:** no new infrastructure; incremental adoption (each milestone below is independently testable); composes with the existing 24h-TTL job records and ADR-0012/0045 webhook policy.
**Cons:** more design + implementation effort than the current model; not justified until operational evidence shows the need.

## Decision Outcome

Adopt **Option A now** and record **Option E as the target direction** for when restart-safe execution becomes a product requirement.

The supported contract, stated explicitly:

1. **Persistence:** job records (status, metadata, payload, results) persist in Valkey for 24 hours (module-level `_default_ttl()` in `agent-svc/agent/store.py`). Status transitions and result data survive a restart; in-flight execution state does not.
2. **Execution:** background work runs best-effort inside the `agent-svc` process via `TaskTracker`. An orderly shutdown gives in-flight tasks a five-second grace period, then cancels them (ADR-0035). A crash, forced termination, or restart does not resume or reclaim work.
3. **Webhooks:** delivery is best-effort with up to three retries and exponential backoff within the process lifetime (ADR-0012, ADR-0045). Undelivered events are not replayed after restart. Each event carries a unique `webhookId` so receivers can deduplicate.
4. **SLO boundary:** in-flight background execution carries **no durability SLO**. Operators must treat completion as at-least-once-with-verification: after a restart, check for stranded jobs (runbook `docs/runbooks/interrupted-jobs.md`) and re-submit critical work. Job records still in `processing` beyond the configured `CRAWL_MAX_DURATION_SECONDS` / expected job runtime should be treated as stranded and reconciled with `scripts/reconcile-jobs.py`.
5. **Recovery:** `scripts/reconcile-jobs.py` lists `processing` jobs older than `--stale-after` (default 3600s) and, with `--fail`, marks them `failed` (only from `processing`; never overwrites `completed`/`cancelled`/`failed`). Where a cancel endpoint exists (`DELETE /v2/crawl/{id}`, `DELETE /v2/agent/{id}`, `DELETE /v2/batch/scrape/{id}`), operators may cancel instead.

Implementation is **deferred** and is triggered only by operational evidence — e.g., repeated incidents with stranded jobs, partial artifacts, or undelivered webhooks, or an explicit multi-tenant/availability requirement. When triggered, the implementation follows Option E as the target architecture, decomposed into the milestones below. Each milestone is independently testable and lands without changing the contract of the others.

### Milestones (follow-up implementation work, testable)

- **M1 — Execution lease on job records.** Add a liveness/lease field to job metadata (e.g., `lease_expires_at` refreshed by the worker) and a staleness classifier. *Acceptance:* unit tests prove a job with an expired lease is classified stale and a fresh job is not; no behavior change for existing records (absent lease ⇒ treated as active, preserving backward compatibility).
- **M2 — Reconciliation surface.** Promote reconciliation to the API/CLI (e.g., list stale `processing` jobs and a reconcile action) with ADR-0039 CLI parity and alerting hook. *Acceptance:* endpoint + CLI parity test; integration test transitions a stranded job to `failed` exactly once.
- **M3 — Webhook outbox.** Persist delivery intent and outcome in Valkey; replay undelivered events after restart, deduplicated by `webhookId`. *Acceptance:* crash-injection integration test proves undelivered events replay exactly once and duplicate deliveries are suppressed.
- **M4 — Queue-backed worker with leases.** A durable job owner with lease/reclaim, retry policy, cancellation semantics, and idempotency keys; `TaskTracker` call sites migrate to enqueue. *Acceptance:* worker crash tests prove reclaim happens and no job is processed twice; cancellation is honored.
- **M5 — Artifact consistency.** Transactional or compensating writes across Valkey, Qdrant, and other downstream stores. *Acceptance:* partial-write simulation tests prove no orphaned artifacts remain after interruption.

## Consequences

### Positive

- The durability boundary becomes an explicit, dated, operator-facing contract with a stated SLO instead of an implicit assumption.
- Operators gain a documented, tool-assisted recovery path for stranded jobs using the existing stack.
- The durable-execution direction and its tradeoffs are recorded (Option E, milestones M1–M5), so a future implementation does not re-derive the decision and can be decomposed into testable increments (issue #470 AC4).
- No API surface, configuration, or dependency changes; the public quickstart no longer risks implying durable background execution.

### Negative

- Background execution remains best-effort: jobs can be lost on restart, and webhooks are not replayed. The SLO boundary is explicit, not stronger.
- Recovery is manual and operator-initiated until M2 ships.
- Job TTL (24h) is not configurable; the runbook's guidance must reference the hard-coded value (follow-up candidates: make TTL configurable as part of M1).

## Links

- [ADR-0035: Graceful Shutdown for Fire-and-Forget Tasks](0035-graceful-shutdown.md) — the five-second grace window this contract builds on
- [ADR-0012: Webhook Delivery for Async Endpoints](0012-webhook-delivery-for-async-endpoints.md) — best-effort webhook model
- [ADR-0045: Outbound Webhook Destination Validation](0045-outbound-webhook-destination-validation.md) — destination policy and `webhookId` dedup
- [ADR-0039: API-CLI Surface Must Ship Together](0039-api-cli-surface-ship-together.md) — constraint that keeps recovery tooling out of the API surface for now
- [Issue #470](https://github.com/groktopus/groktocrawl/issues/470) — this decision request
- [Issue #458](https://github.com/groktopus/groktocrawl/issues/458) — prior durability-contract clarification (closed by PR #462)
- [Issue #196](https://github.com/groktopus/groktocrawl/issues/196) — RQ machinery removal
- [Discussion #427](https://github.com/groktopus/groktocrawl/discussions/427) — durable job owner concept (graph checkpoints ≠ durable execution)
- [Interrupted Jobs runbook](../runbooks/interrupted-jobs.md) — operator recovery procedure
