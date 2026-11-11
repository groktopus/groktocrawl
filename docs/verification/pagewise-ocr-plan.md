# Pagewise local OCR verification plan

Date: 2026-11-11. Owner: independent QA reviewer. Stage: preimplementation.
Runtime implementation has not begun. This plan defines release evidence, not a claim that checks have passed.

## Requester outcome

A long PDF must be extracted page by page through native text or the already-running local vision model, retaining retrievable evidence and truthful coverage, without saturating the shared GPU or losing completed pages on interruption. Architecture documentation precedes code; legacy paths are removed only where replaced and verified. The release ends with a merged, reviewed candidate deployed to production and checked on a long public report plus explicit raster-only OCR evidence.

## Verification boundaries

1. Domain/unit: selection, ordering, schema, budgets, cache identity and failure classification.
2. Service/contract: FastAPI parse routes, session attachment/citation consumers, adapter responses and scoped authority.
3. Real storage/integration: transactions, process recovery, fencing and global concurrency across independent workers, using the deployed storage technology rather than only fakes.
4. Production: exact released revision, long public PDF, deliberately rasterized pages and retry/resume evidence; rollback prepared before promotion.

Mocks prove orchestration, not OCR quality or storage concurrency. A successful native-text PDF proves neither image OCR nor two-worker exclusion.

## Blocking acceptance matrix

| ID | Observable invariant | Required evidence |
|---|---|---|
| P01 | Each admitted page gets one explicit final disposition; ordered outputs and source page numbers match input. Mixed PDFs cannot hide scanned pages behind a document-wide text threshold. | At least 12-page mixed fixture with native, image-only and blank pages; unique page sentinels and call receipts show OCR only on selected pages. Last-page sentinel must appear. |
| P02 | No successful response silently claims complete coverage after a page cap, timeout, cancellation, rendering failure or truncated model output. | Fault injection for each condition; assert total/processed/failed/skipped counts reconcile, exact page statuses, terminal status and warnings. |
| P03 | OCR receives exactly one bounded page image per call and no conversational history; original page instructions are transcribed, never executed. | Captured adapter payloads, two pages containing conflicting instructions/URLs, no network/tool calls from the model interface. Bound raster dimensions, bytes and output tokens. |
| P04 | Native extraction stays the cheap path; Tesseract fallback is explicit and distinguishable from Gemma output. | Native fixture makes zero Gemma calls. Gemma unavailable/rate-limited/permanently invalid cases follow documented policy with correct engine/provenance. No fake Gemma confidence derived from Tesseract scores. |
| P05 | One OCR admission is shared across documents, processes and replicas for the configured local model resource. | Two real independent workers plus a blocking fake endpoint record max active=1. Kill or stall lease owner mid-call; verify no overlapping replacement dispatch while original is still active or outcome unresolved. Stale owner cannot release successor's lock or publish. |
| P06 | Completed page receipts survive worker restart and are reused only for identical source/render/model/prompt/schema identity within authorized scope. | Stop worker after committed page N, restart/retry, assert pages 1..N not called again. Alter each cache-key input separately and show cache miss. Foreign owner cannot read, reuse or infer private content. |
| P07 | Failure/retry semantics are bounded and truthful. | Per-page retry count, backoff and total deadline tests with deterministic clock; no retry of 400/401/403/malformed schema as transient. Unknown dispatched attempt has recorded policy; do not call it exactly once. Exhaustion does not discard successful pages. |
| P08 | Staged upload bytes are retained until successful complete extraction; interruption cannot destroy the only admitted source. | Crash-window tests before/after result checkpoint and staged upload finalization; duplicate requests must not double-dispatch. Direct/staged paths retain equivalent source digest and filename. |
| P09 | Disconnect, task cancellation, session deletion and ownership loss have explicit cleanup/publication behavior. | Disconnect/cancel/delete during render, OCR and before attachment commit. A late parser result may remain a checkpoint if documented, but cannot recreate a deleted session or claim an unreceived response was delivered. Lock uncertainty policy prevents overlapping dispatch. |
| P10 | PDF/page provenance and citations reference the exact retained extraction and original page, not inferred or invented coordinates. | Exact file digest, extraction digest, page index, engine/model revision and Unicode span readback. Rerun producing different text yields distinct extraction identity. Page anchors resolve start/end without off-by-one errors. |
| P11 | Synchronous parse has explicit bounded resubmission recovery, not an undocumented background-job lifecycle. | Contract tests align page-derived processing budget with client/proxy transport deadlines. Disconnect/resubmit reuses committed pages; in-flight unknown pages remain explicit. No claim of automatic restart/resume, exactly-once execution or an async polling API. |
| P12 | Existing document/session and non-PDF parse behavior remains supported. | Existing parse, staged metadata, session documents/concurrency/storage, document journey and research attachment tests pass; update only assertions superseded by the new documented contract. |
| P13 | Local OCR never silently falls back to a remote LLM alias. | Endpoint/model adapter tests and configuration review; routing independent of general/free aliases. Redacted logs and no document bodies/API keys in metrics/errors. |
| P14 | Old code/infrastructure removal is evidence-driven and rollback-safe. | Inventory each removed symbol/config/dependency/caller; demonstrate replacement tests and compose references. Keep Tesseract/renderer or other dependencies still used. No source/user data deletion. |

## Production acceptance

- Record public report URL, retrieval date, SHA-256, file size and authoritative page count. Prefer a DORA annual report from its official publisher.
- Run the complete native/mixed report through the public supported API and inspect first, middle and last pages plus at least one numeric table. Reconcile every page disposition; do not label native extraction as Gemma OCR.
- Create a clearly labeled raster-only derivative of selected actual report pages, preferably a multi-page selection with a table, footnotes and columns. Record original page mapping. Prove Gemma calls and verify sampled text/numbers against visible source, not another generated summary.
- If the long original is entirely digital, report full-document native coverage separately from OCR sample coverage. Do not extrapolate OCR quality or latency from clean synthetic pages.
- Repeat selected extraction to demonstrate checkpoint/cache behavior. Verify no unintended changes to other GPU consumers, model settings, database/worker data or unrelated services.
- Capture exact deployed commit/image identity, health/readiness, active OCR concurrency, page completion counts and memory/latency observations. Keep measured test conditions; no unsupported throughput promises.

## Regression and readiness gates

Use the repository's normal lint, type, unit/service and container integration CI at the final commit. Real containers run on an authorized Linux host or CI, never the Mac. Independent review must inspect frozen final code with security, ownership, transaction, scope, cancellation, stale-worker and content-provenance boundaries. Material changes after review invalidate that verdict.

Required before merge/deploy: all matrix items are mapped to named tests or explicit runtime evidence, no high/critical findings, no accidental secret or private fixture publication, no unverified storage migration. Required after deploy: public health, exact revision, complete long-document accounting and a genuine image OCR result. Any failed production gate triggers documented rollback and an honest blocked result, not completion.

## Preimplementation verdict

PASS for documentation commit `4f41a1ab84e2da84bf397c17aec9a9ac3a11a724` (equivalent implementation-worktree cherry-pick `b99397b8`), 2026-11-11. Independent QA inspected ADR-0079, specification and rollout/recovery runbook before runtime code.

The synchronous bounded-resubmission contract, scoped source/page identity, success-only staged-upload finalization, page-derived processing budget, explicit unknown-dispatch reconciliation, one-host global admission and partial coverage are testable. No async polling or automatic resume claim is permitted.

Tracked documentation corrections, required by PR readiness rather than blocking implementation:
- Replace ownership-generation language with the chosen same-host lifetime document lock and global in-flight marker fencing.
- Describe atomic SQLite BLOB source retention rather than separate source files if that is the implemented storage format.
- Name configurable initial retention/cap defaults (7 days, 1 GiB), explicitly skipping active/unknown evidence during cleanup and validating rollout capacity.

This gate approves the verification plan and design testability, not implementation, release readiness, or production behavior. Matrix P01–P14 remains pending executable evidence. Real cross-process storage/lock/crash checks are mandatory for concurrency and recovery claims.
