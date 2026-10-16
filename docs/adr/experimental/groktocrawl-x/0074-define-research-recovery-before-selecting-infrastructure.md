# Define Research Recovery Before Selecting Infrastructure

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-04
- Scope: experimental research D5 / W5; `magnus919/groktocrawl-x` only
- Plan: issue [#11](https://github.com/magnus919/groktocrawl-x/issues/11)
- Supersedes: ADR-0047 for the opt-in experimental research route only

## Context and Problem Statement

ADR-0047 honestly documents persisted job records without restart-safe execution,
with Valkey-native leases/outbox as a future direction and Temporal deferred.
The experimental client contract instead needs an explicit outcome after worker
loss. Retained IR, a graph checkpoint or an acknowledged queue message alone does
not define ownership, budget accounting, cancellation or publication consistency.

## Decision Drivers

- Recover acknowledged experimental work after process loss without false success.
- One fenced authority for transitions, retries and cancellation.
- No duplicate committed research effect and no unlimited external retries.
- Reconcile ambiguous effects instead of claiming exactly-once provider execution.
- Atomic publication/terminal notification intent and auditable recovery evidence.
- Minimize permanent services, with a measured justification for added infrastructure.

## Considered Options

| Option | Benefit | Required proof / cost |
|---|---|---|
| Retain in-process execution and manual reconciliation | Smallest stack; honest inherited contract | Does not meet unattended experimental restart target |
| Valkey-native leases, retry ledger and outbox | Reuses current service; inherited target direction | Application owns fencing, persistence configuration, scheduling, retention and cross-store reconciliation |
| Temporal as durable execution owner | Candidate for recorded workflow execution and activity retries | Additional service/operations and versioning; activities still need idempotency and external-store commit reconciliation |
| Persistent graph checkpoints plus application recovery owner | May reuse C runtime persistence | Must also prove dispatch/reclaim, retry ledger, fencing, cancellation and outbox; checkpoints alone do not satisfy this contract |
| PostgreSQL-native execution ledger, leases and outbox if D3 is adopted | Can colocate artifact publication and terminal intent in one transaction | Application must build/test scheduler, reclaim, fairness and operational tooling; database choice does not supply a workflow engine |

Temporal explicitly requires idempotent activity design because activities may
execute again. LangGraph's task guidance also calls for idempotent external calls.
Neither library removes this application's effect-boundary responsibility.
[Temporal activity definition](https://docs.temporal.io/activity-definition),
[LangGraph functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)

## Decision Outcome

Adopt the recovery contract below and select the bounded Valkey-native ledger as
the execution owner for the opt-in experimental research route. PostgreSQL is the
artifact authority selected by the successor work in ADR-0078; Valkey owns admitted
run identity, leases, fencing generations, idempotency receipts, cancellation,
checkpoints, retry state, and compact terminal projections. Reconciliation bridges
an authoritative PostgreSQL publication to its Valkey projection after interruption.

This selection does not bind research orchestration to Valkey. Imperative,
LangGraph, or another future orchestrator must operate through the same execution
and artifact contracts. Temporal remains a future alternative if measured workflow
or operating requirements outgrow the bounded ledger. A PostgreSQL-native execution
ledger is not selected because the passing implementation already isolates the two
authority roles and proves their combined restore behavior without adding a new
service. Candidate evidence includes the complete operating footprint and the
[crash matrix](../../../experiments/research-execution-confirmation.md).

### Recovery target and boundary

For acknowledged experimental runs, loss of an API or worker process must not lose
the admitted request, committed result, cancellation intent or terminal notification
intent. After dependencies recover, the owner must resume eligible work or expose
an explicit terminal failure; it must not leave an undetectable `running` record.
This is a proposed process-crash target with durable storage intact. Host/disk loss,
multi-region failover and disaster recovery are separate targets requiring declared
backup RPO/RTO and restore evidence. No wall-clock recovery SLO is claimed yet.

Before W5 implementation, freeze maximum run age, reclaim delay, retry windows,
lease/heartbeat bounds, provider-call deadlines and retention relationships in an
accepted contract amendment. Before pilot entry, demonstrate those bounds at the
measured W1 capacity. Admission may return success only after durable request and
idempotency receipt commit. Expired/deleted roots cannot be revived during recovery.

### Owner, attempts and fencing

Exactly one logical owner decides a run's next transition. Ownership has a monotonic
fencing generation checked by every authoritative mutation, alongside scope, root
generation and operation input digest. Lease expiry permits takeover; it does not
stop an old process physically. A late owner must be rejected at the commit boundary.
Use the authority's time for lease decisions, not unsynchronized worker clocks.
A storage partition prevents unverified ownership from dispatching new work or
publishing. Already dispatched provider work may finish, but cannot bypass fencing.

Each operation has a stable logical ID, separate attempt IDs, persisted dispatch
intent, reserved budget, retry policy, next eligible time and immutable receipt.
The owner chooses retries; transport/SDK retries must be disabled or explicitly
included in the same attempt/time/cost ceiling. Reclaim does not reset counters or
the original deadline. Cancellation persists before acknowledgement and prevents
new dispatch; children receive cancellation, while uninterruptible calls remain
accounted for. Terminal cancellation and publication use one guarded transition.

### Ambiguous side effects and budgets

Persist dispatch intent before an external call and its result receipt before
advancing dependent work. A crash between provider success and receipt persistence
can leave an unknown outcome. Reconcile through provider idempotency/status support
where available. Otherwise, record `outcome_unknown`; retry only when the adapter's
explicit policy allows duplicate external execution and remaining budget covers it.
Read-only searches may still incur duplicate charges. Do not claim exactly-once
network execution or release uncertain token/cost reservations as unused capacity.
Unsafe or unaffordable ambiguity stops the run with an explicit failure reason.

For deterministic internal mutations, same operation ID/input returns the original
receipt; conflicting input fails. Non-idempotent effects without reconciliation
are excluded from automatic recovery. Operator repair requires an auditable action,
not silent receipt deletion. Provider/model changes do not make an old attempt safe
to retry under a different semantic identity.

### Publication, terminal state and outbox

ADR-0071 owns evidence/IR/render receipts. D5 must bind the final publication receipt,
terminal transition and webhook notification intent to one authoritative commit.
If colocated, use a transaction; if execution history and evidence live in different
stores, use an idempotent publication command and reconciliation protocol. The
artifact authority commits its terminal/publication/outbox record; the execution
engine's terminal view is a projection that can catch up after interruption.
Do not require an impossible cross-store atomic write or report success from a
workflow checkpoint before authoritative publication. Persisted cancellation must
reach that same authority before it is acknowledged as accepted; a race with
publication returns the winner's state per ADR-0072.

Notification delivery is at least once within a declared finite retry window, not
guaranteed receiver success. Outbox entries carry stable IDs and references, no raw
research text. Revalidate destinations and root access at dispatch. Record attempts,
ACKs and exhaustion; manual replay preserves notification identity and authorization.
A lost receiver ACK may cause a duplicate. Delivery failure does not change research
outcome. Missing notification intent after publication is a contract violation.

### Retention, upgrades and rollback

Run/attempt receipts, fencing and cancellation state must outlive all eligible work,
late-writer windows and notification retries. Checkpoints reference scoped immutable
artifacts; deleting a session cannot erase an active research owner's state. Root
deletion cancels/revokes eligible work and prevents replay of retained text; keep
minimal tombstone/receipt metadata according to D3. Never restore pending work as
runnable until ownership, deletion inventories and policy versions are reconciled.

Pin admitted policy, state schema, runtime and adapter versions. Drain or retain
compatible workers for old runs; migrate only through versioned, validated tooling.
On unsupported history/state, quarantine for explicit failure/reconciliation rather
than rerun from scratch. Rollback disables new experimental admission while allowing
safe cancellation/status access and reconciliation of already admitted work. It
must not redirect unfinished experimental runs into inherited `/v2` processing.

## Inherited Decision Impact

| ADR | Proposed relationship and scope on acceptance |
|---|---|
| 0047 | Partially supersede experimental restart deferral and predetermined Valkey direction; inherited endpoints retain their documented limitation until separately migrated |
| 0012, 0045 | Extend terminal delivery with durable intent/deduplication; retain destination validation |
| 0035 | Retain graceful shutdown as an optimization, extend recovery beyond its window |
| 0051 | Extend admission/cancellation across owners, waits and recovery attempts |
| 0038 | Retain inherited crawl execution; no crawler migration included |
| 0063, 0066 | Retain session mutation contracts; independent durable research ownership is separate |

The accepted scope is only the opt-in experimental route. Inherited `/v2` endpoints
keep ADR-0047's restart limitation. Any migration of those endpoints or replacement
of Valkey requires a new decision supported by measurements.

## Consequences

The target is testable without treating a vendor feature as proof. Implementations
must carry more state, tooling and operational responsibility than inline tasks.
Conservative ambiguity handling can fail runs rather than risk unbounded spend.
External duplicates remain possible; committed artifacts and terminal outcomes must
remain consistent. Sharing a database may simplify atomicity while concentrating
load/failure risk, which the comparison must measure.

## Confirmation

Magnus owns the target and reviews candidate evidence. The W5 implementer owns
repeatable process-kill, partition, stale-owner, duplicate-delivery and upgrade tests.
Run the shared matrix on every recovery/adapter change and before migrations.
Require zero lost acknowledged intents, duplicate committed effects, unauthorized
publications or false completions in the declared schedules. Missing/untested crash
points are pending, never green. Freeze timing/resource limits before measurement;
record commits, backend durability settings, workload, failures and exclusions.

Escalate failures to the decision owner. Instrumentation failures are indeterminate;
exceptions need scope, compensating controls, approver and expiry and cannot waive
publication/access invariants. Revisit after a recovery incident, adapter ambiguity,
retention/version change or topology change. Retire only with a successor contract
and retained test history.

The bounded Valkey owner passed admission replay, fencing, checkpoint and terminal
recovery, cancellation races, provider ambiguity, the complete declared crash
matrix, and backup/restore. PRs #135, #137, #139, #142, #144, #146, and #148 contain
the incremental implementation evidence. PR #244 moved artifact bytes to
PostgreSQL while preserving compact Valkey projections. Runtime CI run
[34404714550](https://github.com/magnus919/groktocrawl-x/actions/runs/34404714550)
then passed the combined PostgreSQL/Valkey restore rehearsal, including completed,
deleted, and interrupted-publication states. These fixtures cannot prove every
possible failure schedule or production disaster recovery; incidents, new side
effects, topology changes, and retention changes trigger review.

## Links

- [Experiment plan](../../../experiments/research-architecture.md)
- [ADR-0047](../../0047-defer-restart-safe-execution.md)
- [ADR-0071](0071-store-research-evidence-independently-of-sessions.md)
- [ADR-0072](0072-expose-verified-research-through-an-experimental-protocol.md)
- [ADR-0073](0073-compare-research-runtimes-under-one-policy.md)
- [ADR-0078](0078-define-durable-research-backup-and-artifact-authority.md)
