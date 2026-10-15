# Admit Private Documents as Session Evidence

- Status: proposed; implementation prepared for review
- Deciders: Magnus Hedemark
- Date: 2026-10-07
- Scope: experimental fork issue [#419](https://github.com/magnus919/groktocrawl-x/issues/419)

## Context

Parse already extracts local PDF/office uploads; inherited sessions already retain
full Markdown references, synchronize writes, and expire/delete those references.
There is no reason to create another authoritative document warehouse to connect
these two paths.

## Decision

Admit complete parser output as an explicit document reference
in the existing session store, with separate hashes for uploaded bytes and exact
UTF-8 extracted text. Attach/detach serialize with existing session operations;
admission rejects explicit per-file/per-session limits instead of truncating.

New sessions and staged upload reservations bind to the established
credential-derived caller scope. Existing unowned session capability readers
remain compatible; attaching private documents requires a newly owned session.
Foreign callers receive a non-disclosing not-found response. Payloads cannot choose
their owner. Anonymous access remains the existing shared anonymous scope; this is
not a new per-human identity or login system.

Retain display filename, media type, extraction metadata and exact Markdown
heading/page-marker offsets. Do not infer PDF page anchors from a page count.
Document citation URLs resolve retained session text/spans, never acquire files
from an external or local path. Treat all extracted text as untrusted source data.
The ordinary session question/evidence path selects document and web refs together.
CLI and MCP expose the same admission/read/detach/query operations.

## Scope of Existing Decisions

- Extend ADR-0040 session coordination and ADR-0063 persistence offloading.
- Retain ADR-0071/0075 PostgreSQL retained-research authority unchanged. These
  attachments are explicitly session-lived evidence, not a durable research
  publication, backup guarantee, or longitudinal history authority.
- Retain ADR-0084 independent research roots. No automatic injection of old runs.
- Retain Parse extraction behavior and local-first OCR. No new OCR provider,
  embeddings, GPU work, raw binary archive, format conversion service or permissions.

## Consequences and Validation

Raw bytes are consumed/discarded after parsing; only exact extracted text and
provenance remain. Duplicate bytes are idempotent within a session, retaining the
first admitted extraction and filename. Detaching removes the source ref; historical
answers may still contain quotations, so complete removal uses session deletion.
Document refs share session TTL refresh/deletion and cannot outlive that authority.
Fixture tests exercise real local PDF table/DOCX heading parsers, exact Unicode
spans, mixed web/document questions, foreign scope, duplicate bytes, expiry,
concurrent quota admission, deletion and cancellation. Runtime CI verifies the
actual service boundaries; no production deployment is part of this change.

## Links

- [Private document guide](../../../guides/private-documents.md)
- [ADR-0071](0071-store-research-evidence-independently-of-sessions.md)
- [ADR-0084](0084-retain-independent-research-roots-over-default-threads.md)
