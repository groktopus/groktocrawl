# Pagewise PDF Extraction Contract

Status: implementation contract for [ADR-0079](../adr/0079-extract-pdf-evidence-page-by-page.md);
not a claim that the routes below are already deployed.

## Ownership and interfaces

Keep synchronous `POST /v2/parse` and existing CLI/MCP behavior. No new async job
endpoint or webhook contract is introduced. Re-submission of the same source under
the same scope and extraction policy reuses committed page results. This does not
promise work will continue after a client disconnect; only committed checkpoints
are recoverable. Once disconnected, do not start more work where cancellation is
observable; a started operation may still finish and must retain its known receipt.

`agent-svc` derives `X-Parse-Scope` as SHA-256 of authenticated request scope; callers
cannot supply a trusted value. Forward it through generic parse and session document
paths; parse-svc is private-only. Cache IDs, state paths and lookup keys include scope.
Never share existence/output/timing information across scopes. Private checkpoints
are not exposed through a new unscoped API.

Preserve `ocr=local|hosted` compatibility. Normalize query/form forwarding consistently:
the current agent sends multipart while parse-svc reads query parameters. Support the
existing query spelling and multipart form, rejecting conflicting values. Dedicated
`PARSE_OCR_BACKEND=tesseract|gemma` selects the local backend; no public model override
or silent external fallback is permitted. A local force-OCR evaluation option may be
provided, explicitly identified in output; it must not relabel native extraction.

Persist original bytes and execution identity before model dispatch. Do not consume
staged uploads before work starts: retain them until complete success or their
existing documented expiry. Record original source digest separately from extracted
content digest and preserve current session attachment limits/authorization. Existing
source retention APIs must not be repurposed as binary PDF storage without a separate
contract change.

## Configuration contract

Use `PARSE_OCR_BACKEND`, `PARSE_OCR_BASE_URL`, `PARSE_OCR_MODEL`,
`PARSE_OCR_MODEL_REVISION`, `PARSE_OCR_API_KEY_FILE`, `PARSE_STATE_DIR`,
`PARSE_PAGE_TIMEOUT_SECONDS`, `PARSE_DOCUMENT_MAX_SECONDS`, `PARSE_PAGE_RETRIES`,
`PARSE_RENDER_MAX_EDGE`, and `PARSE_OCR_MAX_TOKENS`.
`PARSE_OCR_BACKEND` defaults to `tesseract`; `PARSE_STATE_DIR` is required for Gemma,
while legacy CPU-only deployments may retain stateless compatibility. Persisted
pagewise mode is the production target, not an implicit claim for stateless callers. Initial proposed defaults are a 30-second page attempt timeout, one retry (two total
attempts/page), 4,096 output tokens, and a document allowance of
`min(1800 seconds, 30 seconds + physical_pages * 60 seconds)`. These are provisional
operational guardrails requiring the rollout measurements, not throughput claims.
The per-document allowance grows with actual page count; the ceiling and attempt
settings are configurable. Queue waits count. Matching configurable agent/session/
CLI/MCP parse transport ceilings use the configured document maximum plus 60 seconds
(initially 1,860 seconds), not the old 120-second bottleneck. Document and
verify production proxy limits. Do not introduce an arbitrary hidden page cap.

## Page contract

Retain original bytes/checkpoints for a documented configurable retention period.
Cleanup only inactive expired executions, never active/uncertain lane evidence; enforce
volume capacity before accepting source bytes and fail explicitly on storage exhaustion.
Checkpoint schema migrations are versioned and non-destructive.

The ledger records execution scope, source SHA-256, original filename/media type, physical
page count, policy/schema version and source retention. A page checkpoint key includes
scope, source digest, one-based physical page index, engine/model identity, model
revision where known, prompt version, rendering policy and output schema. Changed
model/prompt/render policy cannot silently reuse an incompatible OCR result.

Each page contains:

- `page_number` (one-based physical PDF index), page result status and text/Markdown;
- engine (`native`, `gemma`, `tesseract`) and extraction version/provenance;
- attempts, request/result timestamps, latency and bounded failure classification;
- render dimensions/DPI when applicable and source digest;
- warnings such as output truncation, unreadable text or fallback;
- native/Tesseract coordinates only when actually measured. Do not invent bounding
  boxes or numeric confidence for Gemma. Printed page labels, when captured, are
  separate from physical page indexes.

Use page-local native-text quality checks, not whole-document length. Empty/scanned
pages route to OCR; blank pages remain explicitly classified rather than omitted.
A force-OCR mode exists for evaluation without mislabeling ordinary digital extraction.
Render one page at a time with pixel/DPI bounds; never materialize an entire long PDF
as a list of images. Dense-page crops are a later explicit policy, not hidden retries.

The OCR prompt requests faithful transcription and marks illegible text; no summaries,
instructions, inferred values or tool execution. Each call contains one page image.
Bound response tokens; `finish_reason=length` is incomplete, not a successful page.
An empty output is not proof of a blank page. Do not merge native and OCR copies of
the same page as independent evidence. Assembling Markdown preserves page order and
page separators without altering the retained source bytes/digest.

## Execution and failure semantics

SQLite commits source/execution acceptance, page completion and terminal projection
atomically within their respective local transactions. Inference/render/network calls
occur outside transactions. Execution ownership generations fence stale writes. At-least-once
execution scheduling is harmless only when committed page receipts are checked before
inference; duplicate callers must not duplicate active work.

The shared volume lock and durable global in-flight record jointly guard the OCR lane.
An operator reconciliation command must inspect trusted backend running/waiting request
metrics (both zero), acquire the same lock, recheck and record the reconciliation;
health=200 is not idle evidence. No request boolean may clear the marker. Other model
consumers must be quiescent during reconciliation. If metrics cannot prove idle, a
deliberate approved backend restart is the alternative.
A process exit releases the kernel lock but leaves a dispatched/unknown marker. New
workers do not reuse that lane until the backend is demonstrably idle or the backend
has been safely restarted, and the reconciliation is recorded. Operators do not delete
markers blindly. Transient failures with known no-dispatch/completed failure outcomes
can retry within the page attempt/deadline budget; unknown dispatches cannot.

Define distinct queue/admission, render, inference and whole-document deadlines. The
admitted document allowance is computed from actual page count and per-page attempt
allowance plus startup/queue allowance. Progress reports remaining/expired budget;
Explicit caller re-submission admits a new bounded document execution window while
preserving completed pages and per-page attempt history. It cannot reset exhausted
page attempts. Automatic transport retries must not silently create a fresh window. Observable caller cancellation, expired document budget, lost ownership
or an open circuit prevents another dispatch. Fallback remains observable and follows admitted
policy; a missing local endpoint must not fall through to a public/free/general model.

Result coverage distinguishes `complete`, `partial`, `interrupted` and `failed`.
Complete means every physical page has an acceptable result or
explicitly verified blank result. Partial includes every missing/failed page and must
not be treated by downstream research as full source coverage.

## Required evidence

- Mixed native/scanned PDF, blank pages, page 11+, ordered table/numeric transcription.
- Scope isolation; duplicate submission, concurrent callers and idempotency conflict.
- Restart after page completion reuses it; interruption before/after dispatch preserves
  uncertainty and prevents an overlapping OCR call; reconciliation permits resume.
- Disconnect/re-submit races, stale publication fences, unavailable ledger/lock storage.
- Page/document deadline, retry exhaustion, truncation, model error and CPU fallback.
- Existing synchronous parsing, sessions and digests remain compatible.
- Real long-report extraction: page count/coverage and retained page citations. Native
  full-document success alone is not evidence that Gemma read every page.
