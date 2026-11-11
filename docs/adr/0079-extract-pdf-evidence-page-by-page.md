# Extract PDF Evidence Page by Page

- Status: accepted; revised local CPU-only contract, production verification pending
- Deciders: Magnus Hedemark
- Date: 2026-11-11
- Scope: native-text and Tesseract PDF parsing with durable page receipts

## Context

The original parser selected native extraction by document-wide character counts,
then processed at most ten pages through OCR. Mixed PDFs could omit scanned pages.
The synchronous parse route had a 120-second transport timeout and consumed staged
uploads before success. Those boundaries are inadequate for long reports.

An earlier design explored a vision-model OCR adapter. The requester subsequently
selected native text plus Tesseract only: the model added no demonstrated value.
This revision supersedes the unshipped model-routing and remote-dispatch recovery
design before the corresponding code is simplified. The shared model service is
outside this change and must not be reconfigured or redeployed as a consequence.

## Decision

Keep extraction in `parse-svc`, with native extraction selected separately for each
page and local Tesseract for image-only/poorly extracted pages. Render one bounded
page at a time. Optional Camelot enrichment remains page-bounded. No OCR model,
model credentials, generic router fallback, GPU admission, or remote-reconciliation
infrastructure is part of this pipeline. Existing explicit `ocr=hosted` compatibility
is a separate legacy opt-in, not an automatic fallback or the pagewise local path.

Retain the existing synchronous `/v2/parse` API. The agent derives authenticated
caller scope for private parser requests. A parse-owned SQLite ledger on persistent
local storage retains original source BLOBs and completed page receipts. Re-submission
of identical bytes and extraction policy within the same scope reuses completed pages.
This is bounded resubmission recovery, not an async job API or automatic restart.
Staged uploads remain available until complete success or their documented expiry.

Support one parse-service deployment on a host with its processes sharing the same
local volume and POSIX locks. A lifetime document lock prevents duplicate same-document
work. One global local-OCR lock initially bounds CPU OCR concurrency across documents
and processes sharing that volume. Independent hosts/volumes are unsupported, not a
distributed lock implementation. The page worker that owns local subprocess execution
must hold the OCR lock until all relevant child processes are terminated and reaped;
request cancellation or parent death must not release capacity while an orphan is
still executing. Prove this property at the real process boundary.

Native extraction, rendering, Tesseract and optional table extraction have bounded
local child lifetimes. Timeouts/cancellation terminate and reap the process group
before releasing its admission lock. There is no remote-call ambiguity state or
backend-idle reconciliation command. Preserve committed page receipts and bounded
attempt counts after interruption; do not mark an interrupted page complete.
SQLite transactions never remain open across extraction subprocess execution.

Use page-proportional document budgets, bounded retries and matching client/proxy
transport ceilings. Every page has an explicit disposition. Partial output includes
missing/failed pages; no silent ten-page cutoff or false complete result. Retain
original source SHA-256 separately from extracted-content identity, physical page
indexes and engine provenance. Coordinates/confidence exist only when measured.

## Alternatives considered

- Whole-document OCR or document-wide quality selection: rejected because mixed
  sources, interruption and memory need page-local boundaries.
- Vision-model OCR: removed from this implementation scope at requester direction;
  added HTTP/model secrets, GPU contention and uncertain-dispatch recovery without
  demonstrated benefit for the tested material.
- New queue/broker/database server: unnecessary for the supported single-host owner.
- Unscoped cache: rejected because private content must not leak across callers.
- Unbounded CPU subprocesses or releasing locks before killing children: rejected;
  a client timeout must not become uncontrolled background work.

## Consequences and cleanup

Long reports gain coverage, page provenance and reuse of completed work. SQLite is
a durable volume format requiring capacity, expiry and rollback care, not a new
server. Source retention is scoped and configurable; active extraction is never
purged by expiry cleanup. No historical checkpoint or user source is deleted merely
to remove obsolete code. Old model-policy receipts must not be reused under the new
CPU extraction fingerprint.

Remove the replaced document-wide switch, ten-page cutoff, unused model adapter,
HTTP worker, model-key configuration and reconciliation infrastructure after tests
prove their replacements. Preserve Tesseract, Poppler, optional Camelot, non-PDF
converters, explicit hosted compatibility, source retention and unrelated services.

## Confirmation

The revised docs precede simplified runtime edits. See the
[contract](../specs/pagewise-pdf-extraction.md),
[verification plan](../verification/pagewise-ocr-plan.md) and
[runbook](../runbooks/pagewise-pdf-extraction.md). Protected CI, independent review,
real process interruption tests and a long public report plus raster-only OCR sample
must pass before claiming production completion.
