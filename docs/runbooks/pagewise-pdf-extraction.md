# Pagewise PDF Extraction: Rollout and Recovery

The [ADR-0079](../adr/0079-extract-pdf-evidence-page-by-page.md) local pipeline uses
native extraction plus Tesseract. Runtime simplification follows this revised contract;
release evidence must name the actual merged revision and observed results.

## Deployment

- Deploy one parse-service owner per host with its processes sharing a persistent local
  SQLite/lock volume. Verify local POSIX locking and filesystem durability; do not
  claim coordination across independent hosts or volumes.
- No OCR model endpoint, model token, GPU slot or backend-reconciliation job is needed.
  Do not alter the existing shared vLLM service or other consumers.
- Preserve the original PDF as a scoped SQLite BLOB and checkpoint completed pages.
  Default retention is seven inactive days and a 1 GiB logical payload cap; monitor
  free space. Cleanup skips active executions; do not delete old receipts during rollback.
- Start with one globally coordinated local OCR worker. Bound native parsing, page
  rendering, Tesseract and optional Camelot in local subprocesses. Timeouts kill/reap
  children before releasing capacity; validate parent-death cleanup too.
- Default page attempts are 30 seconds each, two lifetime attempts/page. Document
  budget grows with page count up to 1,800 seconds. Parse caller transports allow
  that maximum plus 60 seconds. Configure proxies consistently and record failures.
- Rendering longest edge defaults to 2,000 pixels. Admission rejects more than 1,024
  physical pages explicitly, native output is capped at 10 MiB/page, and output from
  local OCR/table subprocesses is bounded. No silent first-ten-page truncation.
- `PARSE_EXTRACT_TABLES=true` enables optional page-bounded Camelot enrichment; off by
  default. Preserve Tesseract, Poppler and Camelot where used. Remove unused Tabula
  installation and replaced model-specific code/config only after reference review.

## Promotion

1. Run unit/service and real-process interruption/storage tests plus existing parsing,
   staged-upload, session digest/citation and explicit hosted compatibility regressions.
2. Inspect candidate Compose/state mounts; retain prior image/config and compatible
   checkpoint backup. No secrets/private fixtures in source or public evidence.
3. Merge only after protected checks and independent review. Deploy exact merged images,
   not a dirty checkout. Verify health and a mixed fixture including page 11+.
4. Fetch a long public annual report from its publisher; retain URL/date/SHA-256/bytes/
   physical page count. Run normal extraction and reconcile all page dispositions.
5. Rasterize selected real report pages as a clearly labeled image-only derivative,
   retaining original-page mapping. Run Tesseract and compare table values, columns,
   footnotes and first/middle/final evidence to the source. Native-report success alone
   does not prove OCR quality; report native and OCR coverage separately.
6. Repeat extraction to prove checkpoint reuse. Exercise timeout/interruption and verify
   no orphan child overlaps the next OCR worker. Measure page latency, CPU/RAM, queue
   wait and coverage. Do not make GPU/model performance claims for this CPU path.
7. Inventory removals: obsolete model adapter/worker/reconciliation/config and old PDF
   switch/cutoff. Keep source data, existing working non-PDF/hosted paths and unrelated
   services. Record exact production revision, health, rollback and evidence links.

## Recovery and rollback

Re-submit the same scoped source to reuse complete pages. A timed-out/killed page remains
incomplete; attempt history is retained. There is no backend-idle reconciliation step.
Verify local children have terminated/reaped before restoring OCR admission. Do not
clear locks manually while their owner may still run. Repeated local tool failures
remain explicit partial/error outcomes, not a reason to send documents to a model.

For rollback, stop new extraction, allow or terminate/reap current workers, retain the
checkpoint volume, and restore the prior parse image/config. Do not downgrade an
incompatible SQLite schema or delete sources. Preserve any model-related historical
state without activating its removed code path; storage cleanup follows normal
retention, not ad hoc deletion. The vLLM configuration is outside this rollback.

Observe bounded-category metrics for queue wait, active local OCR, page dispositions,
engine, retry/process failures, timeouts and execution age. Do not log private text,
filenames or source URLs as metric labels. Response manifests may report omitted page
receipts due to their metadata bound; full receipts remain retained and coverage must
still reconcile exactly.
