# W9 incumbent compatibility result

Status: **passed — functionally equivalent on the frozen journeys**

The isolated candidate and the incumbent each completed all three repetitions of every declared journey: health, scrape, bounded crawl, search, synchronous and streaming answer, synchronous and streaming agent, webhook delivery, CLI, and MCP. No candidate-only failure or inherited-contract regression was observed. The migration and rollback rehearsal may proceed.

## What matched

| Observation | Candidate | Incumbent | Classification |
|---|---:|---:|---|
| Complete runs | 3/3 | 3/3 | Exact |
| Successful declared journeys | 11/11 in every run | 11/11 in every run | Exact |
| Scraped fixture bytes | 13,358 | 13,358 | Exact |
| Stable scrape content hash | yes | yes | Exact |
| Search results | 3 | 3 | Exact |
| Answer sources | 3 | 3 | Exact |
| Answer citations | 2–3 | 2–3 | Functionally equivalent |
| Completion webhook | received; event and job matched | received; event and job matched | Exact |

Generated prose and streaming token counts varied between calls, as expected from live inference. They are classified as functionally equivalent because both arms returned non-empty grounded results with the required terminal events and citation/source structure.

## Timing observations

These medians describe three home-lab observations per arm; they are diagnostics, not a performance benchmark.

| Journey | Candidate median | Incumbent median |
|---|---:|---:|
| Scrape | 10.3 ms | 52.6 ms |
| Crawl completion | 513.4 ms | 517.3 ms |
| Search | 646.7 ms | 417.3 ms |
| Answer | 3715.3 ms | 6357.7 ms |
| Streaming answer | 4065.8 ms | 7804.0 ms |
| Agent completion | 1014.3 ms | 2031.8 ms |
| Streaming agent | 606.2 ms | 555.3 ms |

One candidate agent call took 20.7 seconds and one incumbent streaming-answer call took 13.4 seconds. With only three runs and shared live model/search dependencies, neither tail observation supports a comparative speed claim.

## Resource observations

The point-in-time container snapshots are reduced in `resource-summary.json`. Candidate memory was 1686.7 MiB before and 1698.6 MiB after. Incumbent memory was 1059.3 MiB before and 1995.4 MiB after. The deployments run on different-capacity machines and the incumbent had unrelated prior activity, so CPU, network, block-I/O, and host-relative percentages cannot support a fair efficiency claim.

The candidate currently carries both PostgreSQL/pgvector and a rollback-only Qdrant. Removing Qdrant remains contingent on the migration and rollback rehearsal rather than this compatibility pass.

## Evidence

- `summary.json` contains the machine-readable verdict and latency ranges.
- `resource-summary.json` contains sanitized point-in-time resource observations.
- `w9-candidate-0.json` through `w9-candidate-2.json` and the corresponding incumbent receipts contain individual run observations.
- `../../../../w9-compatibility-protocol.md` is the protocol frozen before execution.

No API keys, webhook tokens, callback URLs, request bodies, response prose, or container identifiers are retained.

## Limits

This result covers the declared fixture and journeys with three repetitions on one home-lab deployment pair. It does not establish Internet-wide scraping reliability, semantic superiority, scale behavior, long-duration stability, or untested endpoint compatibility. Those claims remain with the later W9 gates.
