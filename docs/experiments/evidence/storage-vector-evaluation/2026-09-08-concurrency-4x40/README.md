# PostgreSQL plus pgvector and Qdrant: mixed-concurrency fixture evidence

**Status:** evaluation-only evidence packet

This packet records the first bounded mixed-concurrency comparison for [issue #182](https://github.com/magnus919/groktocrawl-x/issues/182), after three warm fixture rounds. It is not a production storage decision.

The run used `main` after PR #183 and the [Vector Store Evaluation workflow run 34207002080](https://github.com/magnus919/groktocrawl-x/actions/runs/34207002080), with three warm rounds followed by a 40-operation mixed workload using four independent clients per candidate. The unchanged machine-readable artifact is [result.json](result.json).

## What was tested

The concurrency workload used the same six-record fixture and provider resources as the warm rounds. Each candidate received 40 operations across four independent clients: 30 searches and 10 idempotent upserts, assigned deterministically by operation index. The workers used independent Qdrant clients or PostgreSQL connections. The normal retrieval, scope, duplicate, and deletion gates also passed in all three warm rounds.

| Dimension | Value |
| --- | --- |
| Corpus | 6 records across `alpha` and `beta` scopes |
| Warm rounds before concurrency | 3 |
| Concurrency workers | 4 independent clients per candidate |
| Mixed operations | 40 per candidate: 30 searches and 10 upserts |
| Embedding model | `fixture-vector-3d-v1` |
| Vector dimension | 3 |
| Distance | cosine |
| Artifact run ID | `run_20260908T085424Z` |

Both candidates completed all 40 concurrent operations without provider errors or failed operations.

## Observed result

| Candidate | Throughput (ops/s) | Search mean / p50 / p95 / p99 (ms) | Upsert mean / p50 / p95 / p99 (ms) |
| --- | ---: | ---: | ---: |
| Qdrant | 400.75 | 4.814 / 4.141 / 9.487 / 11.259 | 5.633 / 4.909 / 11.399 / 11.399 |
| PostgreSQL plus pgvector | 1842.36 | 0.689 / 0.443 / 2.194 / 3.645 | 0.987 / 0.624 / 2.664 / 2.664 |

These are one 40-operation sample per candidate on one small fixture and one hosted runner. They show that the harness can exercise independent clients concurrently and preserve tail/error evidence. They are not capacity limits, production SLOs, or a general performance ranking.

## Decision boundary

This packet supports continuing the comparison with a bounded concurrency workload. It does **not** select PostgreSQL plus pgvector, authorize removing Qdrant, change production, or accept ADR-0071. The artifact remains marked `evaluation_only`, with `production_change: false` and `qdrant_removal: false`.

Remaining gates include higher request counts and representative corpora, cold and provider-restart behavior, backup and restore, deletion authority after restore, model and dimension migration, failure injection, resource footprint, and reversible cutover or rollback. Those results must keep the same workload manifest and retain all failures before any adoption ADR is considered.

See the [comparison plan](../../../storage/pgvector-qdrant-evaluation.md), the [fresh-resource packet](../2026-09-08-fresh-3/), and the [warm-round packet](../2026-09-08-rounds-3/).
