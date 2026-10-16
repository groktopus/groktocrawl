# PostgreSQL plus pgvector and Qdrant: fresh-resource fixture evidence

**Status:** evaluation-only evidence packet

This packet records three rounds in which each provider’s isolated collection/table was recreated before every round. It extends the comparison tracked by [issue #163](https://github.com/magnus919/groktocrawl-x/issues/163); it is not a production storage decision.

The run used `main` after PR #180 and the [Vector Store Evaluation workflow run 34202573978](https://github.com/magnus919/groktocrawl-x/actions/runs/34202573978), with `rounds=3` and `fresh_each_round=true`. The unchanged machine-readable artifact is [result.json](result.json).

## What was tested

Each round created a new isolated Qdrant collection and PostgreSQL table while the provider services remained running. This measures repeated resource initialization. It does not simulate process restart, provider restart, recovery, backup restore, or an outage.

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
| Artifact run ID | `run_20260908T080520Z` |

Both candidates passed all provider, retrieval, scope, and deletion gates in all three fresh rounds. There were no provider errors.

## Observed result

Aggregate timings across the three fresh rounds were:

| Operation | Qdrant mean / p50 / p95 (ms) | PostgreSQL plus pgvector mean / p50 / p95 (ms) |
| --- | ---: | ---: |
| bulk upsert | 2.192 / 1.757 / 3.140 | 2.481 / 2.450 / 2.567 |
| duplicate replay | 1.230 / 1.242 / 1.252 | 0.543 / 0.549 / 0.573 |
| filtered search | 1.438 / 1.253 / 2.766 | 0.363 / 0.237 / 0.884 |
| delete | 1.377 / 1.284 / 1.588 | 0.354 / 0.363 / 0.372 |
| post-delete search | 1.935 / 1.231 / 3.343 | 0.231 / 0.231 / 0.233 |

The score tiers and expected IDs matched the fixture reference. Differences inside equal-score groups remained allowed. Scope isolation, duplicate replay, and deleted-document absence passed in every round.

The timings are three repeated initialization passes against one six-record fixture and one hosted runner. They demonstrate that fresh-resource evidence can be captured and compared; they do not establish a production performance winner or a restart/recovery guarantee.

## Decision boundary

This packet supports continuing the comparison with an explicit fresh-resource measurement mode. It does **not** select PostgreSQL plus pgvector, authorize removing Qdrant, change production, or accept ADR-0071. The artifact remains marked `evaluation_only`, with `production_change: false` and `qdrant_removal: false`.

The remaining gates include actual process/provider restart, backup and restore, deletion authority after restore, bounded mixed concurrency, representative scale, model and dimension migration, failure injection, resource footprint, and reversible cutover or rollback. Those results must preserve the manifest and all failure records before any adoption ADR is considered.

See the [comparison plan](../../../storage/pgvector-qdrant-evaluation.md), the earlier [warm-round packet](../2026-09-08-rounds-3/), and the initial [single-round packet](../2026-09-08/).
