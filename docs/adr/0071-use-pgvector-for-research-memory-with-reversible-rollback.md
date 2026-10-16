# Use pgvector for Research Memory with Reversible Rollback

- Status: proposed
- Deciders: Groktopus maintainers
- Date: 2026-10-15
- Scope: research-memory embedding index and semantic page index
- Supersedes: none
- Links: [ADR-0041](0041-research-memory.md), [ADR-0049](0049-research-memory-compatibility-freshness-swr.md), [ADR-0069](0069-adopt-bounded-research-substrate.md), [research-memory storage runbook](../runbooks/research-memory-pgvector.md)

## Context and Problem Statement

Research-memory artifacts are stored in Valkey, while their semantic lookup vectors are hardwired to a Qdrant collection. The semantic page index can serve from pgvector, but its current serving mode still probes and writes Qdrant to preserve immediate rollback. This leaves deployments that select PostgreSQL unable to operate when Qdrant is absent and keeps two vector services in the steady-state topology.

## Decision Drivers

- Preserve the existing Valkey artifact keys, TTLs, response freshness, user scoping, and compatibility fingerprints.
- Keep Qdrant as the default for deployments that have not migrated.
- Provide an explicit idempotent copy and parity check before changing the read authority.
- Make PostgreSQL-only semantic operation independent of Qdrant probes and writes.
- Keep legacy Qdrant and dual-write modes available, and never delete source vectors during migration.

## Considered Options

1. Keep Qdrant as the permanent vector dependency. This avoids migration work but retains an unnecessary service dependency for PostgreSQL deployments.
2. Replace Qdrant immediately. This simplifies steady-state operation but risks cache misses and lost rollback state when existing vectors have not been copied.
3. Add explicit Qdrant, PostgreSQL shadow, and PostgreSQL-only modes with a reversible migration procedure. This preserves deployed defaults and lets operators verify copied state before a cutover.

## Decision Outcome

Adopt option 3. Research-memory artifacts remain in Valkey. PostgreSQL stores a rebuildable pgvector projection with the same memory UUID, query, user scope, compatibility fingerprint, creation/expiry timestamps, and embedding. The default `RESEARCH_MEMORY_VECTOR_MODE=qdrant` keeps current behavior. `shadow_pgvector` keeps Qdrant reads authoritative while mirroring vectors to PostgreSQL. `pgvector` reads and writes the PostgreSQL projection without contacting Qdrant.

The semantic page index keeps its existing `qdrant`, `shadow_pgvector`, and `pgvector` behavior. A new `VECTOR_STORE_MODE=pgvector_only` serves and writes the page index through PostgreSQL, with readiness reporting Qdrant as not required. Its dedicated `pgvector-only` Compose profile enables PostgreSQL and semantic-svc without activating the Qdrant profile. This mode has no immediate Qdrant rollback copy; operators can recreate that copy from the PostgreSQL projection with the explicit restore procedure.

Migration is explicit and additive. The backfill skips expired rows and Qdrant points whose Valkey artifact no longer exists, never deletes or rewrites source Valkey or Qdrant state, and is safe to rerun. Verification checks the active ID set, metadata contract, and stored vectors within a fixed numeric tolerance. A deployment must finish the backfill and parity check before selecting PostgreSQL-only reads. The PostgreSQL projection has an independent schema version and is not coupled to the durable research ledger's migration version.

## Consequences

### Positive

- PostgreSQL-only semantic page indexing no longer needs Qdrant at readiness or request time.
- Operators can migrate the research-memory vector projection without changing Valkey artifacts or API contracts.
- Existing deployments remain on Qdrant until they explicitly opt into migration.

### Negative

- PostgreSQL and Qdrant shadow modes add write and storage cost during migration.
- PostgreSQL-only rollback requires copying current projection rows back to Qdrant before returning reads to Qdrant.
- Operators must verify vector parity and keep the PostgreSQL volume backed up; this ADR does not authorize dropping an existing Qdrant volume.

## Rollback

For research memory, run the `restore-qdrant` migration command to upsert current non-expired PostgreSQL rows whose Valkey artifacts still exist, then return `RESEARCH_MEMORY_VECTOR_MODE` to `qdrant`. The command preserves existing Qdrant points not present in PostgreSQL. For the semantic page index, run the supported PostgreSQL-to-Qdrant restore while retaining the PostgreSQL projection, then set `VECTOR_STORE_MODE=qdrant`. A database or Valkey restore is not part of application rollback.

## Confirmation

Implementation review must cover backfill idempotency, retrieval, writes, deletes, TTL/orphan sweeping, parity verification, missing-backend behavior, and both read and write rollback paths. The deployment runbook must use isolated PostgreSQL tests and must not state that production state has migrated without a live migration receipt.
