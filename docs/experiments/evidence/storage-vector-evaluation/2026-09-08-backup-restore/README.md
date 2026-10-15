# PostgreSQL plus pgvector and Qdrant: fresh-target backup/restore evidence

**Status:** successful isolated recovery rehearsal; evaluation-only

This packet records the successful [Vector Store Backup Evaluation workflow run 34238404432](https://github.com/magnus919/groktocrawl-x/actions/runs/34238404432) on `main`. The workflow seeded a six-record fixture, created a provider-native Qdrant collection snapshot and a PostgreSQL custom-format `pg_dump`, restored them into fresh isolated Qdrant and pgvector services, applied the explicit deletion manifest, and verified retrieval plus deletion continuity.

## Recorded evidence

| Measure | Result |
| --- | ---: |
| Qdrant snapshot | 64,512 bytes |
| Qdrant snapshot SHA-256 | `499f529ba72df3b17a9cb4f25e5901e86bf8f9d66368949974a1357198d413e0` |
| PostgreSQL dump | 3,527 bytes |
| PostgreSQL dump SHA-256 | `8ad1cd408805ba6e9c2220c9d04911b14b11f0dcefc1e16b50cc3939b96a4b67` |
| PostgreSQL restore duration | 133 ms |
| Deletion manifest SHA-256 | `b94f38d51d9a93b4e09840478e778cea223ec90b16a036d4dac8319d8cdba58e` |

Both candidates passed all top-k, scope, provider, and deletion gates after restore. The manifest deleted `doc-001`; the post-restore `alpha-x` result was `doc-002` followed by `doc-003` for both providers. Equal-score ordering remains unspecified within the existing ranking gate.

The raw JSON packets are preserved in this directory. The binary snapshot and PostgreSQL dump remain in the linked workflow artifact so their exact bytes and hashes can be inspected without putting binary backup material in the source tree.

## Limits

This is one hosted run against a six-record fixture and fresh single-node targets. It does not establish production recovery objectives, provider version migration, crash-during-write behavior, resource exhaustion, or a production storage decision. It does not authorize Qdrant removal. A PostgreSQL adoption decision still requires the dedicated ADR and the remaining failure-injection and operational acceptance work.

See the [comparison plan](../../../storage/pgvector-qdrant-evaluation.md), the [restart packet](../2026-09-08-restart/), and the [workflow artifact](https://github.com/magnus919/groktocrawl-x/actions/runs/34238404432).
