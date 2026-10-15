# Bounded canonical JSON admission

ADR-0071's approved exploration now has a representation utility in
`agent-svc/agent/experimental/canonical.py`. `admit_canonical_json` accepts UTF-8
bytes and a caller-selected expected schema version. It returns immutable JCS
bytes and SHA-256 of the expected schema's UTF-8 bytes, a zero byte, and those
canonical bytes. A top-level object with that exact `schema_version` is required.
This is representation admission, not validation of all fields in a research schema.

Serialization uses pinned [rfc8785.py 0.1.4](https://github.com/trailofbits/rfc8785.py),
an Apache-2.0, no-dependency implementation of
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785). Admission rejects duplicate
properties after escape decoding, invalid UTF-8 or Unicode, nonfinite numbers,
fractional/exponent numeric tokens and integers outside ±(2^53−1). Exact decimals
and larger integers must be schema-defined strings. JSON booleans remain booleans.

This first boundary limits input and output to 1 MiB, nesting to 64 containers and
values plus property names to 100,000 nodes. Nesting is checked before recursive
JSON decoding; the node bound is checked before serialization. This is a bounded
prototype document limit, not the final root/source quota or a streaming parser.
The caller already holds input bytes, and parsing allocates objects within the
input bound. A later storage integration must enforce its transport and quota
limits before allocating larger input.

Tests check RFC property ordering (including supplementary Unicode characters),
control-character escaping, Unicode preservation, canonical round trips and schema
separation. A separate serializer test checks the RFC floating-number vector;
those numeric tokens remain intentionally rejected by the admission layer under
the stricter ADR schema policy.

The helper does not normalize source text, authenticate provenance, validate
permissions, establish semantic truth, connect a database or freeze `knowledge-ir/1`.
It is not wired into inherited endpoints. Existing fixture serialization/digests
are unchanged. Tests use `storage-envelope-prototype/1`; applying the final IR or
render digest identifiers remains dependent on complete schema/version validation.
