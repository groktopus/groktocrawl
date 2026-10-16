# Adopt a Bounded Research Substrate

- Status: accepted
- Deciders: Groktopus maintainers
- Date: 2026-10-15
- Scope: research execution and retained-evidence substrate in the standard GroktoCrawl deployment; capability flags are temporary rollout controls
- Provenance: adopted in part from the bounded architecture records in [`docs/adr/experimental/groktocrawl-x/`](experimental/groktocrawl-x/README.md), based on experiment commit `9c7744b66f205080a44bfd89808494dbe80eb5a4`
- Supersedes: ADR-0026's Qdrant serving-store choice for deployments that complete the verified pgvector backfill and cutover. Qdrant remains a rollback-write target. This does not revise ADR-0047 or change the existing public API, job, session, or cache contracts.

## Context and Problem Statement

The research architecture work developed an explicit execution controller, versioned claims-and-evidence records, verification outcomes, and audited artifact rendering. The mainline standard deployment includes PostgreSQL for durable research state, making these capabilities usable in the same deployment as deterministic retrieval, scraping, and existing clients.

The experimental records include decisions accepted for bounded prototypes, evaluations, and use in the former experimental repository. Those acceptances do not establish that every experimental feature is production ready or that existing user data has been migrated. Mainline needs a clear adoption boundary that preserves deployed interfaces and makes state changes reversible.

## Decision Drivers

- Keep existing API, CLI, MCP, job, session, and research-memory behavior compatible by default.
- Keep research routes and pgvector serving disabled during the initial rollout checks, then enable the adopted capabilities in the standard deployment after the documented migration and verification gates pass.
- Separate execution ownership, evidence, verified knowledge, and rendered artifacts so each can be inspected and versioned.
- Avoid implicit conversion, deletion, or reinterpretation of existing persisted state.
- Permit rollback to the existing research path without losing data or changing existing schemas.

## Considered Options

1. **Replace the current research path and persisted contracts immediately.** This would make the new architecture the default quickly, but would conflate bounded experimental acceptance with production readiness and require a broad compatibility break.
2. **Keep the research work in a separate repository.** This isolates implementation but separates it from the mainline interfaces, tests, and delivery process that must govern eventual use.
3. **Adopt the architecture in the standard mainline deployment with bounded rollout gates.** This preserves a single reviewed codebase, keeps existing contracts authoritative, and enables verified capabilities without a separate deployment or repository.

## Decision Outcome

Adopt option 3. The new research substrate provides explicit bounded execution, versioned knowledge and evidence, verification records, and audited renderings in the standard deployment architecture. The `/experimental/research/v1` route family and its existing feature flags remain the rollout boundary while implementation and operations are verified. Its path remains stable for compatibility; the word `experimental` does not create a separate repository or a permanent separate deployment mode. After the required checks pass, the standard deployment enables the supported durable research capabilities without changing the route or moving the service to a different stack.

For vector search, pgvector becomes the serving store only after a complete mainline-Qdrant backfill and the parity and operational checks in the [migration runbook](../runbooks/mainline-architecture-migration.md). The cutover keeps Qdrant online and dual-writes to it so operators can return reads to Qdrant without rebuilding the old index. This narrows ADR-0026 only for deployments that complete this migration; unmigrated deployments continue using Qdrant.

The execution owner is the sole authority for run transitions, budgets, cancellation, retries, and terminal outcomes. Concurrent operations return keyed results for that owner to apply. Evidence records preserve source and capture provenance; knowledge revisions identify claims, support, conflicts, and verification; rendered artifacts identify the knowledge revision they represent. Completion of execution and sufficiency of an answer remain separate outcomes.

All loops, retries, source acquisition, and rendering repair remain subject to explicit limits. A model response cannot increase budgets or permissions. Missing or incompatible evidence, state, or verification versions fail closed or produce an explicitly qualified result according to the operation contract.

The new route family does not replace the existing research, `/v2/answer`, session, job, or research-memory path and does not change their request or response schemas. PostgreSQL is part of the standard Compose deployment and is the durable store for the research capabilities that require it; the feature flags control route and capability availability during rollout. They are temporary rollout controls, not a requirement to operate a separate experimental deployment. A flag being present in an environment inventory does not imply the feature is enabled; after migration checks pass, the deployment enables the supported durable capabilities.

### Compatibility and state migration

Existing inline jobs, sessions, research-memory records, API payloads, CLI flags, and MCP tools retain their current readers and writers. The new durable research ledger applies only to the research route family; it does not make legacy inline jobs restart-safe or change their best-effort contract in ADR-0047. No startup task automatically imports or rewrites legacy records into the new execution or evidence stores. New records must carry explicit schema and policy versions. Schema migrations must be additive and ordered, and must refuse an unknown or incompatible prior schema before modifying it.

Before enabling durable research capabilities, the deployment procedure must identify the affected PostgreSQL schema, take and verify a recoverable backup, record the source and target schema versions, run the explicit ordered migration, and verify representative reads and integrity checks. The vector-store cutover additionally requires a verified Qdrant backfill and parity checks; Qdrant remains current through dual writes for rollback. Any conversion of existing research records requires the resumable migration and reconciliation procedure in the migration runbook, with provenance, counts, integrity checks, and a documented abort path. This ADR does not authorize deletion or reinterpretation of legacy state.

### Rollback

Operators can disable the feature gates and route compatible work through the pre-existing path. For vector reads, set `VECTOR_STORE_MODE=qdrant`; ongoing Qdrant dual writes preserve the rollback index. Rollback does not delete research records, drop migrated columns/tables, or rewrite legacy state. If a schema migration has occurred, rollback uses the prior compatible application reader and retains the additive schema; restoring a backup is reserved for a verified recovery procedure. Any irreversible format change or removal of the old path requires a later ADR and migration plan.

## Consequences

### Positive Consequences

- Research architecture can evolve in the mainline repository without silently changing existing API or storage contracts.
- Every experimental run can carry explicit budget, ownership, evidence, verification, and artifact-version information.
- Operators have a defined rollout, migration, and rollback boundary before default enablement; verified deployments converge on the durable research capabilities and pgvector serving.

### Negative Consequences

- The repository keeps the existing API research path alongside the durable research route family and must keep their compatibility boundaries clear.
- Durable research state requires explicit migration and retention procedures before capability enablement.
- This ADR adopts an architecture boundary, not a claim that every feature, route, migration, or operating procedure is complete or production-ready.

## Confirmation

For each capability, its implementation review must identify the relevant feature flag, public-surface impact, schema/version behavior, migration and rollback evidence, and test coverage. Before enabling it by default in the standard deployment, confirm operator backup/restore evidence, deployment configuration, and required CI gates. Until those checks are complete, documentation must label the API capability experimental and must not present it as a default-enabled guarantee.

## Links

- [Experimental record provenance](experimental/groktocrawl-x/README.md)
- [ADR-0047: Defer restart-safe execution with an explicit job-durability contract](0047-defer-restart-safe-execution.md)
- [Research architecture migration plan](../plans/research-architecture-migration.md)
- [Mainline deployment migration runbook](../runbooks/mainline-architecture-migration.md)
