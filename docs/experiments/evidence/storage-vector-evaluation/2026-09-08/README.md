# PostgreSQL plus pgvector and Qdrant: bounded fixture evidence

**Status:** evaluation-only evidence packet

This packet records one successful hosted run of the isolated PostgreSQL plus pgvector and Qdrant comparison. It is evidence for the experiment tracked in [issue #163](https://github.com/magnus919/groktocrawl-x/issues/163), not a production storage decision.

The run was executed by the [Vector Store Evaluation workflow](https://github.com/magnus919/groktocrawl-x/actions/runs/34194908873) at commit `bdfe241ac2db5c7bfd2f4d1994c6589e0cb37449` on 2026-09-08. The workflow uploaded the unchanged machine-readable [result artifact](result.json).

## What was tested

Both candidates received the same deterministic synthetic fixture and workload sequence:

| Dimension | Value |
| --- | --- |
| Corpus | 6 records across `alpha` and `beta` scopes |
| Queries | 4 filtered searches |
| Embedding model | `fixture-vector-3d-v1` |
| Vector dimension | 3 |
| Distance | cosine |
| Duplicate replay | 2 replays |
| Workloads | bulk upsert, duplicate replay, filtered search, delete |
| Corpus hash | `5817c09ae7b94803b760a3003b967a5a223264dc265f5caf9b773d0da33462b7` |
| Artifact run ID | `run_20260908T063020Z` |

The harness checked provider startup, expected top-k retrieval, scope isolation, and absence of a deleted document. It permits arbitrary ordering inside an equal-score group while requiring the score tiers and expected IDs to remain correct.

## Observed result

| Candidate | Provider and retrieval gates | Errors | Result |
| --- | --- | --- | --- |
| Qdrant | All 10 gates passed | None | Pass |
| PostgreSQL plus pgvector | All 10 gates passed | None | Pass |

The candidates returned the same score tiers and expected IDs for all four queries. The only ordering difference was within zero-score ties, which is intentionally unspecified. Scope filters remained isolated, duplicate replay did not violate the fixture’s expectations, and the deleted document was absent from the post-delete query in both stores.

The single-run timing observations were:

| Operation | Qdrant mean / p50 / p95 (ms) | PostgreSQL plus pgvector mean / p50 / p95 (ms) |
| --- | ---: | ---: |
| bulk upsert | 3.099 / 3.099 / 3.099 | 2.548 / 2.548 / 2.548 |
| duplicate replay | 1.450 / 1.450 / 1.450 | 0.549 / 0.549 / 0.549 |
| filtered search | 1.672 / 1.369 / 2.839 | 0.377 / 0.241 / 0.837 |
| delete | 3.122 / 3.122 / 3.122 | 0.351 / 0.351 / 0.351 |
| post-delete search | 1.186 / 1.186 / 1.186 | 0.226 / 0.226 / 0.226 |

These numbers are fixture timings from one hosted run with one process and one small corpus. They are useful as a smoke signal that both paths completed, but they are not a benchmark or a basis for a performance claim.

## Decision boundary

This run supports continuing the storage comparison. It does **not** select PostgreSQL plus pgvector, authorize removing Qdrant, change the production stack, or accept ADR-0071. The evidence packet is deliberately marked `evaluation_only`, with `production_change: false` and `qdrant_removal: false` in the artifact.

The next evidence required before a storage recommendation includes repeated cold and warm runs, concurrency and throughput, larger representative corpora, duplicate and deletion behavior over time, restart and rebuild behavior, backup and restore rehearsal, dimension/model migration, failure injection, resource footprint, and a reversible cutover or rollback exercise. Each result must preserve the same corpus and workload manifest so the candidates remain comparable.

See the [comparison plan](../../../storage/pgvector-qdrant-evaluation.md) for the gates and planned workload matrix. Keep this packet separate from any future accepted ADR or production migration record.
