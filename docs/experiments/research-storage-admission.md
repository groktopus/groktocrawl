# Bounded experimental storage connections

[Issue #80](https://github.com/magnus919/groktocrawl-x/issues/80) implements a
connection admission prerequisite under accepted [ADR-0071](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md).
This is isolated groktocrawl-x exploration, not production capacity acceptance.

## Shared budget and failure behavior

All `SourceStore` transactions and inherited adapters share one process-wide
`StorageAdmission` with eight slots by default. Reads, writes, receipt lookups,
bootstrap and migrations count alike, across all connection destinations. The
thread-safe guard works across event loops and adapter instances; constructing
another store does not obtain another default budget. Eight is a conservative
prototype limit, not a measured optimum or supported database workload.

A call acquires a slot synchronously before connecting. If all slots are occupied,
`StorageBusyError` is raised immediately: no connection, transaction or waiter is
created for that call. There are no automatic retries. The existing thirty-second
transaction deadline and connection/statement/lock timeouts remain in force after
admission. The slot is held through transaction exit and connection close, then
released on success, SQL/connection failure, timeout or cancellation. A SQL or
connection error keeps its original exception type.

Only `StorageBusyError` means this transaction did not start. Cancellation or a
connection error around COMMIT can still have an ambiguous durable outcome: use
the existing operation receipt protocol. A multi-transaction operation may already
have committed earlier work before a later transaction is denied. Expiry collection
retains its existing partial-progress/error behavior. Admission never retries or
reverses earlier effects.

A trusted owner may supply `admission=StorageAdmission(limit)` when constructing
adapters. Share that same object throughout a deliberately budgeted workload;
creating a new guard for every request defeats aggregate admission. Limits must be
positive integers. Separate explicit guards and separate processes have independent
budgets. Operators must divide the available database connection budget across
processes/workloads and preserve administrative headroom. This mechanism is not a
distributed limiter or pool, and introduces no deployment environment variable.

## Limits of this boundary

This bounds active connection attempts and owned connections, not acquisition
buffers, pre-transaction canonicalization, retained task inputs, process RSS or
all operations against the database. Callers still reserve byte quota before
acquisition and bound buffers and overall dispatch. They must propagate saturation
or retry under their own finite deadline and attempt budget. This layer promises
neither fairness nor reserved publication/administrative capacity.

No database schema, canonical format, retention deadline or quota changes. No
provider work is added under transactions. No default service invokes the adapter.
Measured root/scope capacity, backup footprint, aggregate acquisition admission,
public error mapping and production deployment acceptance remain separate work.

## Verification

Eight local unit cases cover invalid budgets, overflow, exception cleanup,
cross-thread reuse and default sharing across subclasses/destinations. Required
Runtime CI adds five real PostgreSQL cases after schema-9 lifecycle checks:

- Two connections across adapter types reject a burst of twenty additional calls
  before connection setup, then close and permit reuse.
- SQL failure, invalid connection configuration, cancellation and statement timeout
  each release the slot and allow a subsequent real read.

Run on the dedicated harness after its schema is installed:

```sh
docker compose run --rm -T storage-adapter admission
```

Use the explicit [isolated Compose setup](research-postgres-harness.md); never run
against a pilot or mainline database. Existing 263 lifecycle cases and restore
verification remain required, so the job now schedules 268 database cases. The new
cases passed in [PR #81 Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/34031758014),
alongside all 263 existing cases, restore checks and full integration. All five
post-merge workflows passed on `07cb7e5fc9dfb294f7f14cf25072a721e9ff1caf`.
Local guard tests alone would not establish these database results. The burst is a saturation test, not a throughput or latency benchmark.

The next [capacity probe](research-storage-capacity.md) is predeclared separately;
connection saturation evidence does not establish workload capacity.
