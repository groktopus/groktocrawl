# Research execution comparison and recovery confirmation

Proposed companion to [ADR-0073](../adr/experimental/groktocrawl-x/0073-compare-research-runtimes-under-one-policy.md)
and [ADR-0074](../adr/experimental/groktocrawl-x/0074-define-research-recovery-before-selecting-infrastructure.md),
issue [#11](https://github.com/magnus919/groktocrawl-x/issues/11).
**This is a future test specification, not executed runtime or recovery evidence.**

## Sequence and ownership

1. W1 freezes the ADR-0070 baseline, versions, corpus and measurement bounds; obtain
   acceptance of required contracts before their implementation gates.
2. W2 constructs the evidence-first imperative reference. W4 adds only the equivalent
   LangGraph adapter, keeping policy and operation implementations shared.
3. W4 compares runtime conformance/engineering cost. Storage/recovery cases without
   an implementation are explicitly pending and excluded from any durability claim.
4. Before W5 implementation, accept a concrete recovery target with timing/retention
   limits. Shortlist technology candidates using the complete ownership/outbox
   design, not a successful checkpoint demo. Bound the spike scope in its issue.
5. W5 exercises each shortlisted recovery candidate against identical schedules;
   record a separate infrastructure decision before adoption.

Magnus is the decision owner/evidence consumer. W4/W5 implementers own the tests and
reports. Run relevant conformance tests on every adapter, retry, persistence or
state-schema change; rerun the full crash suite before pilot/upgrade acceptance.
Archive exact commit, dependency versions, source/LLM fixture identities, backend
configuration, host limits, event/receipt traces and exclusions. Paid evaluations
remain outside this specification's zero-external-spend fixture lane.

## Runtime conformance matrix

| Stimulus | Required invariant | Observation |
|---|---|---|
| Same scripted inputs in B and C | Same claims, qualifications, logical operations, budgets and artifact revision after stable ID mapping | Canonical semantic diff, not raw timestamp comparison |
| Branch completion order reversed | Stable join/presentation; no last-writer overwrite | Delta/receipt trace and final manifest |
| Duplicate or conflicting operation receipt | Identical receipt deduplicated; conflicting input/output rejected | One committed effect or explicit conflict |
| Retry inside SDK and controller | Declared one-owner budget covers every attempt; no multiplication | Adapter call count plus durable budget ledger |
| Budget exhaustion during fan-out | No unreserved work dispatched; uncertainty preserved | Attempt ledger and terminal result |
| Cancel while queued or in search/browser/LLM/verification | Child cancellation propagated; no late publication after cancellation wins | Operation and terminal transition trace |
| Node emits unaudited prose or framework event | Public contract rejects it | Negative client-trace control |
| Wrong state version or changed reducer | Explicit incompatibility; no silent replay under new policy | Version error and unchanged committed artifact |

Use ADR-0070's repeated paired cold/warm measurements. Separate no-checkpoint,
checkpoint-enabled and recovery-enabled workloads; never attribute persistence
cost to graph scheduling without the matching reference configuration. Record a
small branch/join change in both arms for reviewer assessment. Do not infer quality
from deterministic model fixtures or fabricate numeric developer-productivity gains.

## Recovery crash matrix

Inject process death immediately before and after each named commit/ACK boundary;
include delayed completion from the old owner after takeover. Use controlled fixture
failures, not real third-party side effects. Persist the injected schedule so a
failure can be reproduced and compared across candidates.

| Interruption / race | Required post-recovery behavior |
|---|---|
| Admission commit versus lost response | Same retained idempotency key resolves to one admitted run; no lost acknowledged request |
| Lease expires while old worker runs | New owner may reclaim; stale generation cannot mutate or publish |
| Authority unavailable during heartbeat | No new dispatch/publication without verified ownership; in-flight effects remain accounted for |
| Dispatch intent committed, call not sent | Reconcile/resume under same logical operation and remaining budget |
| Provider succeeded, receipt absent | Reconcile supported provider identity; otherwise explicit unknown outcome with conservative reservation and bounded policy |
| Receipt committed, checkpoint absent | Reuse receipt; dependent work does not repeat completed operation |
| IR committed, rendering incomplete | Retain valid staged/IR data under D3; no successful terminal answer |
| Published render set, engine ACK absent | Artifact authority's terminal/outbox receipt wins; engine projection catches up without another publication |
| Cancellation acknowledged, late publish | Same terminal authority rejects late publication; recorded cancellation survives restart |
| Publication wins before cancellation | Return the committed completed result; never emit a second cancelled terminal |
| Outbox dispatched, receiver ACK lost | Duplicate uses same notification ID; attempt/exhaustion visible; research outcome unchanged |
| Root deleted while work/replay is pending | Fence writers, invalidate reads/replay and suppress text-bearing access; no resurrection |
| Retry window/run age expires during downtime | Explicit terminal outcome; restart does not reset deadlines or counters |
| New binary cannot read pinned state | Quarantine/reconcile or use compatible worker; no silent fresh run |
| Restore predates deletion or cancellation | Quarantine until current deletion/ownership inventories reconcile; no automatic runnable resurrection |

## Candidate report and decision gate

For each shortlisted candidate, report the complete topology: execution owner,
authoritative terminal store, artifact store, lease/fencing mechanism, retry owner,
outbox authority and reconciliation path. Include backup/restore, upgrades, local
deployment steps, idle/load CPU and memory, disk/history growth, recovery delay and
operational diagnosis. PostgreSQL consolidation must include workload interference
with evidence publication and any pgvector comparison; do not assume colocated
components are free. Qdrant consolidation remains governed by ADR-0071.

Hard invariants permit zero violations across the declared schedules. A missing
result, untested boundary or broken fixture is pending/indeterminate, not compliant.
False positives require a documented fixture correction and rerun of affected
cases; retain prior failures. Tests can miss unmodeled schedules, so require review
of the boundary inventory and disclose this limitation. Do not reduce the denominator
or retry until success to pass the gate.

Before implementation, freeze timing limits and retention relationships; before
adoption, compare measurements against those fixed bounds. An exception requires
Magnus's approval, scope, risk, compensating control, expiry and exit evidence; it
cannot permit false completion or unauthorized publication. Review on incidents or
policy/runtime/storage changes. Retire only with an explicit successor and preserved
results. Passing process-crash tests does not establish disk-loss or regional recovery.
