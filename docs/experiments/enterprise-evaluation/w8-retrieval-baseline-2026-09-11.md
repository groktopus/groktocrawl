# W8 retrieval baseline — 2026-09-11

Status: **private packet frozen; current SlopSearX baseline complete; comparison
variants remain open**

W8 begins by measuring whether the acquisition layer can find known useful
evidence for obscure, recent, ambiguous and terminology-shifted questions about
agentic engineering software factories. This first run establishes the current
production SlopSearX behavior. It does not yet select a replacement retrieval
policy or close issue #294.

## Private packet

An independent Hermes Agent run curated 24 held-out queries before any W8
retrieval variant was implemented. The packet contains six queries in each of
four slices: long-tail, recent/as-of-sensitive, ambiguous-entity and
terminology-shift. It identifies 44 useful-source judgments across 28 distinct
canonical HTTPS URLs from primary or authoritative sources. Every case has an
explicit as-of date and an ambiguity note.

Only query metadata, URLs and curator judgments are retained; no copied source
text, credentials or retrieval scores are present. The private packet is frozen
at `sha256:60ae7c92c7f66673185c6ad59dc7236030c93e9238a082a80d36730bee58034d`.
Its questions and judgments remain outside the repository.

## Baseline contract

The baseline ran each query once against the deployed SearXNG-compatible search
route at SlopSearX revision `5622a902`. It requested at most 20 results, used a
30-second request timeout and performed no automatic retries. The runner records
raw private responses, result rank, exact known-source matches, normalized URL
duplicates, cache and partial-result flags, latency and engine diagnostics.

Exact known-source recall is deliberately conservative. It strips fragments,
default ports, `www` and trailing slashes, and handles GitHub path case, but it
does not assume that different pages or mirrors are equivalent. A later source
audit must distinguish genuine misses from useful alternate pages before an
adoption decision.

## Observed result

| Measure | Current SlopSearX |
|---|---:|
| Scheduled / completed | 24 / 24 |
| Known useful URLs found | 17 / 44 (38.64%) |
| Queries with at least one known useful URL | 14 / 24 |
| Mean first useful rank, when found | 2.79 |
| Returned results | 463 |
| Exact normalized duplicates | 0 |
| p50 / p95 latency | 551 ms / 1,103 ms |
| Cached attempts | 0 |
| Partial attempts | 24 |

| Slice | Known useful URLs found | Queries with a match |
|---|---:|---:|
| Ambiguous entity | 6 / 11 (54.55%) | 4 / 6 |
| Long tail | 4 / 10 (40.00%) | 4 / 6 |
| Recent / as-of-sensitive | 6 / 10 (60.00%) | 5 / 6 |
| Terminology shift | 1 / 13 (7.69%) | 1 / 6 |

Every response was partial. Brave returned successfully for all 24 queries, but
DuckDuckGo was challenge-walled for all 24. Reddit was blocked on 15, Google on
14 and Wikipedia was rate-limited on 12. One query selected GitHub without a
configured token. The baseline therefore measures a fast, mostly single-engine
path rather than healthy independent metasearch breadth. Monetary cost remains
unknown because the compatible response does not report it.

## What follows

The next comparison should keep this packet, result limit, deadlines and exact
matching rules fixed while testing eligible acquisition channels and an audited
source-equivalence review. It should report engine health separately from
retrieval quality. W8 planning, diversity and freshness experiments may reuse the
same raw snapshots only when doing so cannot expose the held-out judgments to a
candidate policy.

SlopSearX issue #307 is directly relevant: additive engine provenance,
explainable rank components, canonical clustering, distinct timestamps and
bounded diagnostics would make these measurements more reliable. Those features
must remain optional and compatible for all SlopSearX users. GroktoCrawl-X still
owns research sufficiency, source independence and stopping decisions.

## Evidence identities

| Private artifact | Digest |
|---|---|
| Frozen schedule | `sha256:36d62d056a5731b6198f29651ee818670e5a18eecfaca24212c5af91ff2d2a0f` |
| Raw result ledger | `sha256:3f9969477765c936197c3bb14f5940c39882d9d3779ad79898242c7e01544159` |
| Aggregate manifest | `sha256:5dcd29f37c753f4f00f1c694cd5120b4a3a28d83dded9b580b354e78fa4f4077` |

This baseline changes no production configuration and makes no adoption claim.

