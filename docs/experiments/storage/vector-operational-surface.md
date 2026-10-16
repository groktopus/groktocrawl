# Vector-store operational surface comparison

Date: 2026-09-09  
Scope: experimental `magnus919/groktocrawl-x` decision support

## Decision being prepared

Should the experimental architecture keep Qdrant as its semantic index, or run
a bounded pgvector pilot that could remove Qdrant if PostgreSQL also becomes the
retained-evidence authority?

The current evidence supports building the bounded pilot. It does not support
removing Qdrant yet. Consolidation reduces the number of databases only when
PostgreSQL is already required for retained evidence and durable artifacts. If
that adoption is rejected, pgvector merely trades one database for another.

## Observed current surface

The checked-in `indexing` profile starts `semantic-svc` and Qdrant. Qdrant has a
dedicated persistent volume, health probe, image/version lifecycle, four-GiB
memory limit, timeout configuration, backup/restore procedure, and model-index
migration path. `semantic-svc` depends directly on the Qdrant client across its
startup, indexing, search, retention, and model-migration modules. The agent's
research-memory implementation is a second direct Qdrant HTTP consumer.

The home-lab deployment on `incumbent.example.internal` confirmed the same Qdrant and semantic
service definitions on 2026-09-09. At the observation point, Qdrant was healthy,
used 21.68 MiB of its four-GiB limit, and its volume held 478,883,056 bytes.
Those are point-in-time inventory facts, not capacity or cost measurements.

The experimental retained-evidence work currently uses an opt-in PostgreSQL
profile. Its adoption as the production artifact authority remains undecided in
ADR-0078. Therefore the service-count benefit from pgvector is conditional:

| Shape | Durable databases | Main operational consequence |
|---|---:|---|
| Current indexing profile | Valkey + Qdrant | Mature working vector path; separate Qdrant upgrades, backup, volume, health, and incidents |
| PostgreSQL authority + Qdrant | Valkey + PostgreSQL + Qdrant | Strong workload isolation, but three data services and coordinated recovery/deletion across them |
| PostgreSQL authority + pgvector | Valkey + PostgreSQL | One fewer service and one recovery boundary; vector load can compete with publication and durable-work queries |

## Evidence affecting the choice

Both candidates passed the bounded correctness, repeatability, concurrency,
restart, backup/restore, injected-failure, 1,024-dimensional scale, sustained
mixed-load, dimension-migration, deletion-continuity, cutover, and rollback
checks indexed in the [comparison plan](pgvector-qdrant-evaluation.md).

At 10,000 records and 5,000 mixed operations per round, Qdrant produced steadier
and higher median throughput. pgvector used roughly half the sampled memory, but
its throughput and tail latency varied materially. These component runs do not
show whether either store meets end-to-end service objectives or whether shared
PostgreSQL vector load interferes with authoritative publication.

## Bounded pilot recommendation

Add a storage adapter behind the existing semantic-service API and support three
explicit modes in the experimental profile:

1. `qdrant`: current behavior and rollback destination;
2. `shadow_pgvector`: Qdrant remains authoritative while bounded writes are
   copied to pgvector and sampled reads are compared without serving them; and
3. `pgvector`: allowed only after reconciliation and interference gates pass.

The pilot must keep embedding inference and the public semantic API unchanged.
It must record stable IDs, model identity, dimensions, payload metadata,
deletion state, source/target write outcomes, sampled top-k differences, and
latency separately. A failed shadow write or comparison is visible evidence and
cannot silently change the authoritative Qdrant result.

Before any `pgvector` serving mode, the pilot must demonstrate:

- zero cross-scope or deleted-result violations;
- complete backfill and continuous ID/model/deletion reconciliation;
- restart and restore with deletion authority reapplied before reads resume;
- bounded connection, CPU, memory, WAL, and index-maintenance demand;
- no material interference with retained-evidence publication or durable job
  recovery under the same PostgreSQL limit; and
- a tested switch back to the preserved Qdrant collection.

## Current recommendation

Proceed with `shadow_pgvector` in the experimental profile. Keep Qdrant as the
authoritative and served index until the shared-database interference and
application-level reconciliation evidence passes. If PostgreSQL is not adopted
as the artifact authority, retain Qdrant because the consolidation benefit
disappears. If the pilot passes and PostgreSQL is adopted, propose a new ADR to
serve from pgvector and remove Qdrant from the experimental deployment. Volume
deletion remains a separate explicit cleanup after the rollback window.
