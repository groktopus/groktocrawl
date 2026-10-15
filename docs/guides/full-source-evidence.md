# Inspect retained evidence

Grounded answers and research use query-relevant, verbatim passages selected from
full permitted source text. `evidence_budget_chars` defaults to 32000 and accepts
integers 256–128000. It bounds total selected Unicode characters per synthesis call;
source headers and metadata add context overhead. It does not limit acquisition or
retention. Lexical selection can miss paraphrases or split a table.

API `/v2/agent` and `/v2/answer`, CLI `agent`/`answer --evidence-budget-chars`, and MCP
`agent`/`answer` accept the optional budget. Session query/deepen steps accept it in
`params`; query `params.ref_ids` explicitly narrows refs and rejects missing/empty
retained text. Responses expose `evidence_coverage`, including stable content-bound
snapshot identities, whole-content hashes, exact span/quote hashes, selected/omitted
totals and recovery instructions. Source text remains unchanged. `complete` means
all supplied text was selected; `coverage_complete` remains false because exhaustive
answer coverage is never established. Cached and streamed responses preserve this
metadata, and cache compatibility includes selection version and budget.

Use `GET /v2/session/{session_id}/evidence/{ref_id}` to inspect a retained ref without
fetching or generating an answer. `query` selects relevant passages; omit it to read
contiguous pages. `budget_chars` has the same range; `offset` is a Unicode character
offset, not a byte offset. `expected_digest` pins a continuation to the original
SHA-256. Each span contains exact `quote`, `start`, `end` and `quote_digest`.
A `continuation` object supplies the next GET and digest precondition. Query selection
with omissions points to offset0 so callers can inspect the entire body. Query and
nonzero offset together are invalid. Changed content returns409; foreign, deleted,
expired or raced-deleted references return404. Existing authentication applies.

CLI: `groktocrawl --json evidence SESSION REF --query 'current price'
--budget-chars 1800`; omit the query and use `--offset` / `--expected-digest` for
paging. MCP `select_session_evidence` exposes the same fields as a read-only action.
Private refs never enter a new shared index. Newly created sessions are owned by the
existing request credential scope; legacy unowned session IDs retain opaque-ID
compatibility.

Experimental `prepare_source_passages` still rejects construction exceeding120KB,
32 passages or8 sources with `EvidenceAdmissionLimitError` and classification
`EVIDENCE_ADMISSION_LIMIT`. Its recovery points to `prepare_query_passages` over
already-authorized retained snapshots with an explicit budget and narrowed sources.
This separates retained-byte admission from context construction and does not
activate a new research controller. Use the scoped page endpoint to inspect omitted
session text. Independent highlights, summaries and enrich calls have separate
per-call budgets and return transformation-specific coverage.
