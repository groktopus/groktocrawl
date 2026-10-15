# Retain source search provenance and scholarly metadata

* Status: proposed; implementation prepared for review
* Deciders: Magnus
* Date: 2026-10-03

## Context

SlopSearX groups publications by trustworthy work identity before reranking.
The search client and subsequent model reconstruction previously dropped engine
lists and scholarly fields, so the consumer lost the producer's evidence of
source agreement. Enrichment and hybrid blending repeated this loss.

## Decision

Extend ADR-0043's SearXNG compatibility contract in this experimental fork.
Retain `engine` (string) and `engines` (array of strings) end to end through
client parsing, fast/deep/hybrid paths, content enrichment and result serialization.
Retain only type-checked optional SearXNG Paper fields: DOI identifier string,
authors/journal/publisher/editor, bibliographic fields, PDF/HTML URL strings,
comments/type, ISSN/ISBN/tags arrays and volume. Preserve `publishedDate` only
when reported as a string. Lists and strings have explicit bounds.

The consumer never infers work identity or chronology from a title, retraction
notice or model judgment. SlopSearX owns its upstream work grouping. The public
result has one URL; alternate URLs/DOIs remain producer internal metadata.
No arbitrary upstream payload or alternative array is exposed. No source mapping
fetches or additional model calls are introduced. Existing search/ranking,
retrieval and authentication settings remain authoritative.

New metadata is additive and optional. Missing metadata retains the legacy
serialized shape, including the existing content-enrichment null fields. A
singular engine supplies a one-element engines array when the upstream array
is absent. Malformed metadata is omitted. Existing ADR bodies remain immutable;
this record extends compatibility scope, not runtime/artifact authority.

## Consequences

Source agreement and scholarly fields remain available to callers after retrieval
and enrichment. Additive metadata increases result size within explicit bounds.
Consumers that reject unknown optional fields may need to update their schemas.
Missing or malformed identifiers still limit producer grouping; this consumer
change does not establish publication validity or independent relevance.

## Validation and rollout

Hermetic fixtures cover client request count, public serialization, malformed
metadata, hybrid blending, fast/enriched routes and deep search. Required CI and
approving review precede normal merge. Deploy the reviewed consumer before the
producer, preserve existing environment/volumes and record immutable rollback
images. Check actual public same-query results and source arrays on both hosts;
health checks alone are insufficient evidence of usefulness.

Reference: [SearXNG Paper](https://docs.searxng.org/dev/result_types/main/paper.html).
