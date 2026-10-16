# W8 freshness, identity, and link-rot outcome

- Date: 2026-09-11
- Issue: [#297](https://github.com/magnus919/groktocrawl-x/issues/297)
- Scope: frozen synthetic acquisition contract; no production adoption
- Packet schema: `w8-freshness-packet/1`
- Packet cases: 16, with two cases in each of eight scenario classes
- Packet SHA-256: `18c433513fe78fcbe869d2cad577fe2014ebad13ae83900790e53622a19d761c`
- Packet freeze digest: `7634dc92545d240690f778886a80555a9dcda33ab4f677d84e653ddc24c8cf43`
- Result freeze digest: `999ab65795dbf6f556cb608a3ac972ced4ef84f3349560d61809656387cc4131`

## Question

Can GroktoCrawl-X preserve the exact page, identity, dates, and acquisition history
needed by its evidence contracts when search results are stale, redirected,
duplicated, missing, or in conflict?

The comparison uses an independently prepared private packet covering current
pages, stale snippets, redirects, canonical aliases, updated pages, syndicated
copies, dead links, and competing versions. The runner exposes each frozen response
through a loopback HTTP server. It therefore exercises HTTP status handling,
redirect traversal, headers, and exact response bytes without contacting or
publishing the fictional sources.

Two arms process the same cases:

- **Snippet trusting** accepts a nonempty discovery snippet and records only the
  URL and dates supplied by search.
- **Exact fetch** follows the response chain, retains every observed URL and check
  time, reads the terminal response bytes, and records canonical and source-date
  metadata without filling unknown dates.

## Results

| Measure | Snippet trusting | Exact fetch |
|---|---:|---:|
| Correct accept/reject decisions | 14/16 | 16/16 |
| Stale snippets accepted as evidence | 6 | 0 |
| Exact current body | 4/16 | 16/16 |
| Correct canonical identity | 2/16 | 16/16 |
| Correct link-rot handling | 14/16 | 16/16 |
| Correct published timestamp | 0/16 | 16/16 |
| Correct modified timestamp | 7/16 | 16/16 |
| Unknown dates preserved | 10/16 | 16/16 |
| Full acquisition provenance preserved | 14/16 | 16/16 |
| Duplicate membership correct | 14/16 | 16/16 |
| Material conflicts exposed | 0/2 | 2/2 |
| Median processing time | 1 microsecond | 4.653 milliseconds |
| 95th-percentile processing time | 20 microseconds | 5.543 milliseconds |

The timing measures only the local contract fixture and runner. It shows the
software overhead is bounded; it does not estimate Internet fetch latency.

Exact retrieval observed ten snippet/body differences, although only six cases
contained stale snippets and two represented competing versions. A search snippet
is often a summary rather than a copy of page text. Text inequality is therefore
useful change evidence but cannot, by itself, classify freshness or contradiction.

## Decision

Adopt exact retrieval and immutable response retention as requirements of the
experimental evidence path. Search snippets remain discovery hints. They cannot
serve as current evidence when the underlying page has not been checked.

Keep discovery, publication, modification, retrieval, and check times separate.
An unavailable value remains null. Follow redirects while retaining every observed
URL and status. Resolve duplicates for selection by canonical identity and exact
body digest, while preserving every acquisition record. Keep both the discovery
representation and fetched version when they differ so a later verifier can decide
whether the difference is summarization, an update, or a material conflict.

This result confirms a contract on a synthetic packet. It does not demonstrate
accuracy of publisher-supplied dates, establish Internet-scale latency, or authorize
changes to the default GroktoCrawl stack.

The remaining W8 source-equivalence audit is tracked in
[#307](https://github.com/magnus919/groktocrawl-x/issues/307).

## SlopSearX boundary

SlopSearX can help by returning additive, nullable metadata when an engine actually
provides it:

- discovery time;
- source-reported publication and modification times, including their provenance;
- canonical or final URL when already observed;
- response status and check time when SlopSearX performed a fetch;
- exact-content digest when calculated from retained bytes;
- publisher/origin and lineage hints.

These fields must remain optional and backward compatible. SlopSearX should not
declare a result fresh merely because its snippet differs from fetched text.
GroktoCrawl owns reacquisition, trust policy, evidence acceptance, conflict review,
and provenance retention. SlopSearX issues
[#307](https://github.com/magnus919/SlopSearX/issues/307) and
[#309](https://github.com/magnus919/SlopSearX/issues/309) are compatible places to
develop generally useful receipts and entity hints.

## Reproduction

Run `scripts/run_w8_freshness_acquisition.py` with the frozen private packet, its
SHA-256 value, and a new output directory. The script refuses an existing output
directory or mismatched packet digest. It emits case-level private results and an
aggregate manifest. The public repository contains the runner, tests, and this
aggregate outcome; private packet content and case-level outputs remain outside it.
