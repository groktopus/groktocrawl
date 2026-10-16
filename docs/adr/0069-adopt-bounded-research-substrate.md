# Adopt a Bounded Research Substrate

- Status: accepted
- Deciders: Groktopus maintainers
- Date: 2026-10-15
- Scope: research execution and retained-evidence substrate in the standard GroktoCrawl deployment; scripted fixture-run admission remains disabled in deployments
- Provenance: adopted in part from the bounded architecture records in [`docs/adr/experimental/groktocrawl-x/`](experimental/groktocrawl-x/README.md), based on experiment commit `9c7744b66f205080a44bfd89808494dbe80eb5a4`
- Supersedes: ADR-0026's Qdrant serving-store choice for deployments that complete the verified pgvector backfill and cutover. Qdrant remains a rollback-write target. This does not revise ADR-0047 or change the existing public API, job, session, or cache contracts.

> Historical boundary: this decision records the initial 1.0.0 adoption.
> [ADR-0070](0070-execute-live-durable-research.md) describes the subsequent live
> execution correction; its acceptance remains tracked in the
> [1.0.1 cleanup plan](../plans/release-1.0.1-cleanup.md). The fixture-only statements
> below describe the original adapter, not the corrected implementation.
> [ADR-0071](0071-use-pgvector-for-research-memory-with-reversible-rollback.md)
> extends storage to research memory, and
> [ADR-0072](0072-valkey-leased-legacy-job-recovery.md) updates legacy job recovery.

## Context and Problem Statement

The research architecture work developed an explicit execution controller, versioned claims-and-evidence records, verification outcomes, and audited artifact rendering. PostgreSQL is integrated into the standard Compose deployment for durable retained records and the separate vector migration. The run endpoint remains a scripted example until the live acquisition and publication adapter is implemented; a shared database alone does not make those research capabilities operational.

The experimental records include decisions accepted for bounded prototypes, evaluations, and use in the former experimental repository. Those acceptances do not establish that every experimental feature is production ready or that existing user data has been migrated. Mainline needs a clear adoption boundary that preserves deployed interfaces and makes state changes reversible.

## Decision Drivers

- Keep existing API, CLI, MCP, job, session, and research-memory behavior compatible by default.
- Keep scripted fixture-run admission disabled in deployments with `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false`. Existing four research/workspace/artifact flags retain their read, status, workspace, and recovery behavior and may remain enabled to serve retained records. This development-only flag is not a production live-research switch; a future live adapter must be integrated and separately verified. Pgvector serving is a separate vector-store migration and does not depend on research flags.
- Separate execution ownership, evidence, verified knowledge, and rendered artifacts so each can be inspected and versioned.
- Avoid implicit conversion, deletion, or reinterpretation of existing persisted state.
- Permit rollback to the existing research path without losing data or changing existing schemas.

## Considered Options

1. **Replace the current research path and persisted contracts immediately.** This would make the new architecture the default quickly, but would conflate bounded experimental acceptance with production readiness and require a broad compatibility break.
2. **Keep the research work in a separate repository.** This isolates implementation but separates it from the mainline interfaces, tests, and delivery process that must govern eventual use.
3. **Adopt the architecture in the standard mainline deployment with bounded rollout gates.** This preserves a single reviewed codebase, keeps existing contracts authoritative, and enables verified capabilities without a separate deployment or repository.

## Decision Outcome

Adopt option 3. The research substrate defines explicit bounded execution, versioned knowledge and evidence, verification records, and audited renderings in the standard deployment architecture. The current `/experimental/research/v1` run adapter is a scripted fixture demonstration; it does not acquire live sources for the submitted objective. Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false` in deployments, while preserving the existing research/workspace/artifact flags and read access for retained records as required. This opt-in is only for development/demo fixture admission, not production live research. A live adapter must connect the existing research acquisition path to this execution, evidence, verification, and publication flow and pass separate product acceptance checks before production run admission is considered. The route path remains stable for compatibility; its name does not require a separate repository or deployment. Pgvector serving is independent of the fixture route and follows the vector migration below.

For vector search, pgvector becomes the serving store only after a complete mainline-Qdrant backfill and the parity and operational checks in the [migration runbook](../runbooks/mainline-architecture-migration.md). The cutover keeps Qdrant online and dual-writes to it so operators can return reads to Qdrant without rebuilding the old index. This narrows ADR-0026 only for deployments that complete this migration; unmigrated deployments continue using Qdrant.

The execution owner is the sole authority for run transitions, budgets, cancellation, retries, and terminal outcomes. Concurrent operations return keyed results for that owner to apply. Evidence records preserve source and capture provenance; knowledge revisions identify claims, support, conflicts, and verification; rendered artifacts identify the knowledge revision they represent. Completion of execution and sufficiency of an answer remain separate outcomes.

All loops, retries, source acquisition, and rendering repair remain subject to explicit limits. A model response cannot increase budgets or permissions. Missing or incompatible evidence, state, or verification versions fail closed or produce an explicitly qualified result according to the operation contract.

The new route family does not replace the existing live research, `/v2/answer`, session, job, or research-memory paths and does not change their request or response schemas. PostgreSQL is part of the standard Compose deployment and supports retained research artifacts as well as the independent pgvector migration. Database availability or schema version 16 does not make the fixture adapter production research. Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false` in deployments; the existing four research flags may remain enabled for retained reads and recovery. A pgvector cutover can proceed independently of the research flags.

### Compatibility and state migration

Existing inline jobs, sessions, research-memory records, API payloads, CLI flags, and MCP tools retain their current readers and writers. The new durable research ledger applies only to the research route family; it does not make legacy inline jobs restart-safe or change their best-effort contract in ADR-0047. No startup task automatically imports or rewrites legacy records into the new execution or evidence stores. New records must carry explicit schema and policy versions. Schema migrations must be additive and ordered, and must refuse an unknown or incompatible prior schema before modifying it.

The current fixture-backed run endpoint is not an operational capability to enable. A future live adapter requires a separately reviewed migration and acceptance plan that covers the ordered schema migration, backup and restore, objective-dependent HTTP/MCP execution, retained evidence and artifact reads, and crash, budget, and cancellation behavior. The vector-store cutover is independent: it requires a verified Qdrant backfill and parity checks, and Qdrant remains current through dual writes for rollback. Any conversion of existing research records requires the resumable migration and reconciliation procedure in the migration runbook, with provenance, counts, integrity checks, and a documented abort path. This ADR does not authorize deletion or reinterpretation of legacy state.

### Rollback

Operators can disable the feature gates and route compatible work through the pre-existing path. For vector reads, set `VECTOR_STORE_MODE=qdrant`; ongoing Qdrant dual writes preserve the rollback index. Rollback does not delete research records, drop migrated columns/tables, or rewrite legacy state. If a schema migration has occurred, rollback uses the prior compatible application reader and retains the additive schema; restoring a backup is reserved for a verified recovery procedure. Any irreversible format change or removal of the old path requires a later ADR and migration plan.

## Consequences

### Positive Consequences

- Research architecture can evolve in the mainline repository without silently changing existing API or storage contracts.
- The research design gives runs explicit budget, ownership, evidence, verification, and artifact-version information when its live adapter is completed.
- Operators have separate migration and rollback paths for the live-research foundation and pgvector serving; vector cutover does not require enabling the fixture route.

### Negative Consequences

- The repository keeps the existing API research path alongside the durable research route family and must keep their compatibility boundaries clear.
- Durable research state requires explicit migration and retention procedures before capability enablement.
- This ADR adopts an architecture boundary, not a claim that every feature, route, migration, or operating procedure is complete or production-ready.

## Confirmation

For pgvector, confirm backup/restore evidence, exact backfill parity, Qdrant rollback writes, deployment configuration, and required CI gates before cutover. Keep `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false` while the route is fixture-backed; preserve the existing research flags needed for retained-record access. This opt-in must not be used for production research. A future live adapter requires separate HTTP/MCP, retained-byte, crash-recovery, budget, and cancellation acceptance before its production run admission is enabled. Documentation must continue to identify the current route as a scripted demonstration until then.

## Links

- [Experimental record provenance](experimental/groktocrawl-x/README.md)
- [ADR-0047: Defer restart-safe execution with an explicit job-durability contract](0047-defer-restart-safe-execution.md)
- [Research architecture migration plan](../plans/research-architecture-migration.md)
- [Mainline deployment migration runbook](../runbooks/mainline-architecture-migration.md)
