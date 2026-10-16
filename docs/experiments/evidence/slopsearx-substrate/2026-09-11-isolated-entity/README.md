# W11 isolated entity-projection evidence

The corrected live pilot passed result-conservation, relationship-validity,
and flat-snapshot immutability gates for one CVE and one package case.

The CVE observations had already collapsed to one URL-deduplicated result
before projection. The package result projected into separate PyPI-release and
GitHub-repository entities connected by a `candidate_repository` relationship.
That relationship does not assert repository ownership.

Neither case exposed a safely avoidable page acquisition. W11 therefore does
not authorize automatic fetch suppression from entity membership. The useful
live behavior demonstrated here is organization and explicit relationship
modeling while preserving the original flat snapshot.

Queries, URLs, payloads, and result records remain private. The public record
contains hashes, counts, namespaces, relationship types, and gate outcomes.
