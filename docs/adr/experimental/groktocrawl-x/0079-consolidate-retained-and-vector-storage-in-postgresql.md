# Consolidate Retained and Vector Storage in PostgreSQL

- Status: accepted
- Deciders: Magnus Hedemark
- Date: 2026-09-09
- Scope: experimental research architecture in `magnus919/groktocrawl-x` only
- Plan: D3 / W3; issues [#201](https://github.com/magnus919/groktocrawl-x/issues/201) and [#230](https://github.com/magnus919/groktocrawl-x/issues/230)
- Extends: [ADR-0071](0071-store-research-evidence-independently-of-sessions.md) and [ADR-0078](0078-define-durable-research-backup-and-artifact-authority.md)
- Partially supersedes: the Qdrant persistence choice in [ADR-0026](../../0026-phase2-vector-index.md) for the experimental deployment only

## Context and Problem Statement

ADR-0071 permits an isolated PostgreSQL exploration for retained research evidence
and requires evaluating pgvector before keeping PostgreSQL and Qdrant as permanent
services. The experiment now has provider comparisons, fault replay, sustained
resource measurements, migration and rollback evidence, an application shadow
adapter, reconciliation, and shared-database measurements.

The first application pilot exposed brief asynchronous shadow lag. The follow-up
measured 240 writes and 360 comparisons under concurrent retained-artifact work.
All comparisons matched, every write completed within the 75 ms histogram bound,
and reconciliation restored exact parity after a PostgreSQL outage. The evidence
supports a cutover design; it does not prove production capacity or authorize an
irreversible migration.

## Decision Drivers

- Keep one transactional authority for retained evidence, metadata, and semantic
  vectors when PostgreSQL becomes the artifact authority.
- Preserve the semantic API, cosine scoring, model identity, deletion behavior,
  bounded failure handling, and rebuild path.
- Avoid operating and recovering a third data service without a measured need.
- Keep every transition reversible until application-level parity and rollback
  have passed.
- Separate durable evidence authority from rebuildable vector projections.

## Considered Options

| Option | Benefit | Cost or reason not selected |
|---|---|---|
| Keep Qdrant and PostgreSQL permanently | Workload isolation and inherited behavior | Three data services, two backup/deletion paths, and no measured requirement that needs Qdrant |
| Use pgvector for experimental serving with Qdrant rollback | Removes one steady-state service after rehearsal while preserving a reversible path | Shares database resources and requires an explicit adapter, migration, monitoring, and rollback window |
| Remove Qdrant immediately | Smallest deployment surface | Skips application cutover and rollback evidence and makes recovery unnecessarily risky |

## Decision Outcome

Adopt the second option for the experimental fork. Implement a PostgreSQL
serving adapter behind the existing semantic-service boundary, then rehearse an
experimental cutover with Qdrant retained as the rollback target. PostgreSQL is
authoritative for retained artifacts. pgvector remains a rebuildable projection;
loss of its index cannot delete or invalidate retained evidence.

The cutover sequence is:

1. Freeze the image, model, corpus, resource limits, and acceptance bounds.
2. Reconcile Qdrant into an isolated pgvector generation and verify active IDs,
   model identity, deletion state, and pinned query responses.
3. Run dual comparison until normal traffic has no unexplained divergence and
   shadow completion/backlog remains within the declared bounds.
4. Switch only the experimental semantic serving adapter to pgvector.
5. Repeat API response, deletion, restart, restore, and retained-artifact checks.
6. Rehearse rollback to the unchanged Qdrant generation.
7. End dual writes only after the rollback window and evidence packet are reviewed.

Do not delete Qdrant data as part of cutover. Removing the service, dependency,
configuration, and volume is separate work after the rollback window. The
inherited mainline and production deployment remain unchanged.

## Consequences

The target experimental stack has PostgreSQL for durable research data and vector
projections, Valkey for bounded execution/session coordination, and the existing
semantic service for model inference. Backup and deletion procedures have one
durable data authority, while vector indexes can be rebuilt from retained records
or exported compatible vectors.

Vector workload can contend with artifact publication. Connection admission,
statement timeouts, index maintenance, resource alerts, and independent workload
metrics are required. If the application rehearsal exceeds the declared bounds,
loses deletion continuity, or produces unexplained result divergence, roll back
to Qdrant and record the failed gate.

## Confirmation

The proposal is supported by the indexed packets in the
[pgvector/Qdrant evaluation](../../../experiments/storage/pgvector-qdrant-evaluation.md),
the [first application pilot](../../../experiments/evidence/storage-vector-evaluation/2026-09-09-application-shadow-pilot/),
the [sustained lag packet](../../../experiments/evidence/storage-vector-evaluation/2026-09-09-shadow-lag-sustained/),
and the [application cutover and rollback packet](../../../experiments/evidence/storage-vector-evaluation/2026-09-09-pgvector-serving-cutover/).
The application gate and the W9 candidate migration/rollback rehearsal have passed.
ADR-0079 is accepted for the experimental fork. Qdrant remains available through
the W9 operational rollback window and is selected for removal from the experimental
steady-state stack only if that window passes. The decision does not change
production or mainline.

## Links

- [Research architecture plan](../../../experiments/research-architecture.md)
- [Vector operational-surface comparison](../../../experiments/storage/vector-operational-surface.md)
- [W9 candidate migration and rollback result](../../../experiments/evidence/replacement-rehearsal/2026-09-11-migration-rollback/)
- [pgvector shadow pilot protocol](../../../experiments/storage/pgvector-shadow-pilot.md)
- [ADR-0071](0071-store-research-evidence-independently-of-sessions.md)
- [ADR-0078](0078-define-durable-research-backup-and-artifact-authority.md)
