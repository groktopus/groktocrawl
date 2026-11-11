# Pagewise PDF Extraction Contract

Revised contract for [ADR-0079](../adr/0079-extract-pdf-evidence-page-by-page.md).
The local pipeline is native text plus Tesseract only. This document precedes the
runtime simplification and is not itself production-verification evidence.

## Interfaces and scope

Keep synchronous `POST /v2/parse` and existing CLI/MCP behavior. No async job,
webhook or polling API is introduced. Explicit re-submission of identical source
bytes under the same scope/policy reuses committed page results. It does not promise
background completion after disconnect or automatic replay following a restart.

The agent derives `X-Parse-Scope` as SHA-256 of authenticated caller scope and forwards
it through generic parsing/session document paths. External callers cannot choose a
trusted scope. Parse-svc remains private-only. State keys include scope; no cross-scope
result access, existence disclosure or reuse of private content is allowed.

Preserve `ocr=local|hosted` compatibility, with consistent query/form normalization
and rejection of conflicting values. `local` means native-text extraction and local
Tesseract, not a configurable model adapter. `hosted` remains a separate explicit
legacy opt-in; missing local tools must never invoke it implicitly. Force-OCR mode is
an evaluation option and must label engine/selection truthfully.

Retain original bytes before extraction and staged upload bytes until complete success
or documented expiry. Preserve original file digest separately from extraction digest,
current session limits and authorization. Source storage for text-only research
artifacts is not repurposed as a binary-PDF store.

## Budgets and storage

Retain `PARSE_STATE_DIR`, `PARSE_STATE_TTL_SECONDS`, `PARSE_STATE_MAX_BYTES`,
`PARSE_PAGE_TIMEOUT_SECONDS`, `PARSE_DOCUMENT_MAX_SECONDS`, `PARSE_PAGE_RETRIES`,
`PARSE_RENDER_MAX_EDGE`, and optional `PARSE_EXTRACT_TABLES`. Remove model endpoint,
model revision, API-key, response-token and model-backend selection configuration.
Legacy CPU-only deployments may use stateless compatibility; production page receipts
require the configured durable state volume.

Initial configurable guardrails: 30 seconds per page attempt, one retry (two lifetime
attempts/page), a document allowance of `min(1800 seconds, 30 + physical_pages * 60)`,
and a 2,000-pixel rendered longest edge. Queue wait counts against remaining allowance.
These are operational bounds requiring rollout evidence, not throughput promises.
Agent/session/CLI/MCP transport ceilings use document maximum plus 60 seconds
(initially 1,860 seconds); verify reverse-proxy limits align. PDF admission rejects
more than 1,024 pages explicitly, rather than truncating. Native page text is bounded
at 10 MiB. Bound local output for OCR/table workers as well.

SQLite source BLOBs/page receipts expire after seven days of inactivity by default;
logical retained payload is capped at 1 GiB with filesystem headroom monitored too.
Cleanup skips active/locked executions. Capacity exhaustion rejects admission rather
than deleting active work. Version schema migrations; preserve prior receipts/source
bytes and prevent incompatible policy fingerprints from being reused. Old unused
model-policy state is not a reason for destructive migration.

## Page evidence

Checkpoint identity includes caller scope, original source SHA-256, physical page
index, extraction/engine version, rendering policy and schema. Changing selection or
OCR settings invalidates reuse. Each result records physical one-based page number,
status, engine (`native` or `tesseract`), attempts, timing, original source identity,
render dimensions where applicable and specific warnings. Table enrichment is marked
separately, not claimed as original text. Native/Tesseract coordinates and confidence
are included only when measured; printed page labels differ from physical indexes.

Select extraction per page. Preserve usable sparse native text if OCR fails, but mark
the page/result degraded rather than erasing evidence or claiming full extraction.
A blank page requires explicit evidence; empty extraction alone does not prove blank.
Render one page at a time with bounded pixels, not the full document in memory.
Do not double-count native and OCR copies as independent evidence. Assemble Markdown
in physical page order with page markers while retaining original byte identity.

Response receipts share invariant provenance at document level and omit repeated page
text. A 48 KiB manifest ceiling reports `page_results_omitted` explicitly if exceeded;
full page receipts remain in retained state and all processed text remains in Markdown.
This keeps 142-page metadata compatible with the session's 64 KiB cap without implying
that a response manifest contains evidence it omitted.

## Process ownership and recovery

One same-host shared-volume document lock serializes identical document work. A shared
local-OCR lock admits one OCR worker initially across distinct documents/processes.
Use a lock-owning worker lifetime that covers render/Tesseract child execution: terminate
and reap the process group on deadline, cancellation or interrupted ownership before
capacity is released. Parent exit must not leave an orphan OCR child executing after
another worker obtains the lock. A file-lock unit test alone is insufficient; exercise
this with actual child processes. No remote dispatch marker, backend-idle probe or
reconciliation command remains in the CPU pipeline.

Commit acceptance, page receipts and final coverage within local transactions, but
run subprocesses outside them. Re-submission checks committed receipts before starting
work and preserves attempt counts. Explicit caller re-submission may admit a new
bounded document window; automatic HTTP retries must not silently reset budgets or
exhausted page attempts. Interrupted work cannot publish a successful page receipt.
Cancelled/disconnected callers cannot trigger further pages where cancellation is
observable; a completed late checkpoint is reusable, not proof a response was delivered.

Classify local tool absence, malformed/encrypted PDF, timeout, process failure and
output exhaustion. Retry only bounded retryable failures; keep successful pages.
Coverage states distinguish complete, partial, interrupted and failed. Complete requires
every physical page to have an acceptable result or verified blank disposition.
Downstream sessions reject partial admission rather than presenting full coverage.

## Session snapshot compatibility

A session keeps its first admitted extraction for identical original bytes. Repeat
attachment returns that existing snapshot with `duplicate: true`; a new session is
needed to adopt changed extraction without altering citation offsets. Generic parsing
may return a policy-versioned extraction independently.

## Required evidence

Mixed digital/scanned pages; page 11 and final-page coverage; sparse/blank text; scoped
cache isolation and concurrent duplicate submissions; killed child cleanup before lane
release; completed-page reuse after restart; bounded retry/deadline/output failures;
retained staged uploads; no model/remote fallback; existing sessions/non-PDF/hosted
compatibility; long public report accounting and a real raster-only Tesseract sample.
