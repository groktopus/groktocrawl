# PostgreSQL plus pgvector and Qdrant: three-round fixture evidence

**Status:** evaluation-only evidence packet

This packet records the first hosted three-round comparison after the harness gained round-level evidence. It is a repeatability check for [issue #163](https://github.com/magnus919/groktocrawl-x/issues/163), not a production storage decision.

The run used the merged `main` commit `1ef7b6500a7be85a478f99c115ae86f1f8a2151c` and the [Vector Store Evaluation workflow run 34199280801](https://github.com/magnus919/groktocrawl-x/actions/runs/34199280801). The workflow was invoked with `rounds=3`; the unchanged machine-readable artifact is [result.json](result.json).

## What was tested

The three rounds reused the same initialized provider resources. Round 1 followed provider initialization; rounds 2 and 3 were warm passes. The fixture remained deterministic:

| Dimension | Value |
| --- | --- |
| Corpus | 6 records across `alpha` and `beta` scopes |
| Queries | 4 filtered searches per round |
| Embedding model | `fixture-vector-3d-v1` |
| Vector dimension | 3 |
| Distance | cosine |
| Duplicate replay | 2 replays per round |
| Workloads | bulk upsert, duplicate replay, filtered search, delete |
| Corpus hash | `5817c09ae7b94803b760a3003b967a5a223264dc265f5caf9b773d0da33462b7` |
| Artifact run ID | `run_20260908T072617Z` |

Both candidates passed all provider, retrieval, scope, and deletion gates in all three rounds. There were no provider errors.

## Observed result

Aggregate timings across the three rounds were:

| Operation | Qdrant mean / p50 / p95 (ms) | PostgreSQL plus pgvector mean / p50 / p95 (ms) |
| --- | ---: | ---: |
| bulk upsert | 2.137 / 1.650 / 3.414 | 2.453 / 2.154 / 3.410 |
| duplicate replay | 1.357 / 1.347 / 1.536 | 0.638 / 0.673 / 0.675 |
| filtered search | 1.454 / 1.267 / 3.067 | 0.261 / 0.193 / 0.939 |
| delete | 1.907 / 1.294 / 3.203 | 0.458 / 0.357 / 0.669 |
| post-delete search | 1.276 / 1.200 / 1.465 | 0.189 / 0.191 / 0.227 |

The score tiers and expected IDs matched the fixture reference. Differences inside equal-score groups remained allowed. Scope isolation, duplicate replay, and deleted-document absence passed in every round.

The timings vary across the three passes and come from one six-record fixture, one process, and one hosted runner. They show that the harness can repeat and preserve evidence; they do not establish a performance winner, capacity bound, or production latency expectation.

## Decision boundary

This packet supports continuing the comparison with round-level evidence. It does **not** select PostgreSQL plus pgvector, authorize removing Qdrant, change production, or accept ADR-0071. The artifact remains marked `evaluation_only`, with `production_change: false` and `qdrant_removal: false`.

The next gates remain bounded mixed concurrency, explicit cold-start and restart behavior, larger representative corpora, expiry and orphan cleanup, backup/restore, model and dimension migration, failure injection, resource footprint, and reversible cutover or rollback. These must retain the same manifest and preserve failure records before any adoption ADR is considered.

See the [comparison plan](../../../storage/pgvector-qdrant-evaluation.md) and the earlier [single-round packet](../2026-09-08/).
