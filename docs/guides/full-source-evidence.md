# Inspect retained evidence

Research and grounded answers supply **all retained source text by default**.
`evidence_budget_chars` is an optional aggregate excerpt budget: when set to an
integer of at least 256, it selects query-relevant verbatim passages across the
provided sources. There is no application-level upper ceiling. This option
changes synthesis input; it never changes acquisition or the retained source
bodies. Lexical selection can miss paraphrases or split a table, so inspect its
coverage and use the complete retained evidence when auditing omissions.

The model provider may accept less text than the complete corpus. GroktoCrawl
first attempts synthesis with all selected source text. If the provider reports
a context-window limit, the system hierarchically extracts source-labeled notes
from every source block, then synthesizes from those notes. Completion coverage
reports `evidence_coverage.synthesis.mode` as `direct` or `hierarchical`, plus
the number of reduction calls and that the source text was retained. A note is a
model transformation, not verbatim evidence; citations still refer to the
original sources. If an individual source cannot be processed within the
provider's limit, the request returns a structured `MODEL_CONTEXT_LIMIT` error
instead of a partial answer.

API `/v2/agent` and `/v2/answer`, CLI `agent`/`answer --evidence-budget-chars`,
and MCP `agent`/`answer` accept the optional budget. Session query/deepen steps
accept it in `params`; query `params.ref_ids` explicitly narrows refs and
rejects missing/empty retained text. Responses expose `evidence_coverage`,
including stable content-bound snapshot identities, whole-content hashes,
exact excerpt span/quote hashes, selected/omitted totals and recovery
instructions. `complete` means all supplied text was selected;
`coverage_complete` remains false because exhaustive answer coverage is never
established.

Agent and answer results retain complete acquired bodies in a credential-scoped
session for 24 hours. Completion metadata includes `evidence_session_id` and
`evidence_recovery`; each source recovery action supplies the session/ref,
digest-pinned page request, and offset. The source export remains optional. If
session retention fails, the response explicitly reports that and includes the
full inline source bodies as a fallback. Cached artifacts do not contain source
bodies and report `evidence_recovery.status=not_retained`; request a fresh run
to create retained evidence. Research-memory cache compatibility includes the
evidence-selection version and explicit budget.

Use `GET /v2/session/{session_id}/evidence/{ref_id}` to inspect a retained ref
without fetching or generating an answer. `query` selects relevant passages;
omit it to read contiguous pages. `budget_chars` is a per-page budget (default
32,000; minimum 256, no application-level upper ceiling), not a total-source
limit. `offset` is a Unicode character offset, not a byte offset.
`expected_digest` pins a continuation to the original SHA-256. Each span
contains exact `quote`, `start`, `end` and `quote_digest`. A `continuation`
object supplies the next GET and digest precondition. Query selection with
omissions points to offset 0 so callers can inspect the entire body. Query and
nonzero offset together are invalid. Changed content returns 409; foreign,
deleted, expired or raced-deleted references return 404. Existing
authentication applies.

CLI: `groktocrawl --json evidence SESSION REF --query 'current price'`; omit the
query and use `--offset` / `--expected-digest` for paging. MCP
`select_session_evidence` exposes the same fields as a read-only action.
Private refs never enter a new shared index. Newly created sessions are owned by
the existing request credential scope; legacy unowned session IDs retain
opaque-ID compatibility.

Experimental `prepare_source_passages` still rejects construction exceeding
120KB, 32 passages or 8 sources with `EvidenceAdmissionLimitError` and
classification `EVIDENCE_ADMISSION_LIMIT`. Its recovery points to
`prepare_query_passages` over already-authorized retained snapshots with an
explicit budget and narrowed sources. This separates retained-byte admission
from context construction and does not activate a new research controller.
Independent highlights, summaries and enrich calls have separate per-call
budgets and return transformation-specific coverage.
