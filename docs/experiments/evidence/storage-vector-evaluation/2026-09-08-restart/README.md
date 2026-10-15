# PostgreSQL plus pgvector and Qdrant: provider-restart evidence

**Status:** evaluation-only restart evidence

This packet records a successful isolated provider restart test for [issue #185](https://github.com/magnus919/groktocrawl-x/issues/185). It verifies that the seeded collection and table remain usable after restarting both provider containers. It does not establish backup/restore authority or a production storage decision.

The run used the [Vector Store Restart Evaluation workflow run 34214606635](https://github.com/magnus919/groktocrawl-x/actions/runs/34214606635) on `main`. The workflow seeded both providers, restarted the Qdrant and PostgreSQL containers while retaining their isolated volumes, then reconnected without recreating the collection or table. The raw [seed artifact](seed.json) and [post-restart verification artifact](verify.json) are preserved unchanged.

## What passed

Both candidates passed all provider, top-k, scope, and deletion gates before and after the restart. The verification phase found the expected records after restart, kept foreign scopes out of results, and confirmed that deleting `doc-001` produced the expected post-delete result.

| Candidate | Seed phase | Post-restart verification | Provider errors |
| --- | --- | --- | --- |
| Qdrant | All gates passed | All gates passed | None |
| PostgreSQL plus pgvector | All gates passed | All gates passed | None |

The post-restart `alpha-x` result was `doc-002` followed by `doc-003` for both candidates. Equal-score ordering remains treated as unspecified within the existing ranking gate.

## Limits

This is one restart of two provider containers, one small six-record fixture, and one hosted run. It does not test process crash during a write, index rebuild, backup/restore into a fresh target, deletion authority after restore, provider version migration, resource exhaustion, or concurrent traffic during restart. No restart duration or recovery-time claim is made.

The artifact remains evaluation-only, with no production change and no Qdrant-removal decision. Continue with the backup/restore and failure-injection gates before considering an adoption ADR.

See the [comparison plan](../../../storage/pgvector-qdrant-evaluation.md), the [fresh-resource packet](../2026-09-08-fresh-3/), and the [mixed-concurrency packet](../2026-09-08-concurrency-4x40/).
