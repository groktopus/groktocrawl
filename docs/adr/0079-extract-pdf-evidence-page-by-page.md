# Extract PDF Evidence Page by Page

- Status: accepted; implementation and production verification pending
- Deciders: Magnus Hedemark
- Date: 2026-11-11
- Scope: local-first PDF parsing and its resumable execution boundary

## Context

The current parser chooses native extraction using document-wide character counts,
then sends at most the first ten pages through Tesseract. Mixed digital/scanned
PDFs can therefore omit evidence. `/v2/parse` waits up to 120 seconds, and consumes
staged uploads before parsing: neither behavior provides resumable long-document
processing. Parse output is flattened Markdown with document-level metadata.

A deployed local Gemma vision endpoint can transcribe an individual page without
loading another model. Initial synthetic table and skewed-column tests demonstrate
capability, not superiority over Tesseract or representative production throughput.
The shared GPU also serves other requests. Long documents must not monopolize it,
replay every completed page after interruption, or manufacture complete coverage.

## Decision

Keep document extraction in `parse-svc`; do not introduce an OCR broker, GPU model
service, or database server. Use a parse-owned SQLite ledger and source files on a
persistent local volume. This deployment is **one parse service on one host**;
processes sharing its volume coordinate through file locks. Horizontal placement
on independent hosts/volumes is unsupported and must not be advertised as globally
serialized. The service must fail closed if its ledger/lock volume is unavailable.

Implement the contract in [Pagewise PDF extraction](../specs/pagewise-pdf-extraction.md).
The agent remains the public authorization boundary and forwards a derived caller
scope to the private parser. Keep the existing synchronous `/v2/parse` contract;
re-submitting the same bytes under the same scope/policy reuses committed pages.
This is bounded resubmission recovery, not a background job API or general job
resumability. There is no new queue, webhook or legacy job replay behavior. Original
source bytes are retained before inference and staged uploads remain until complete
success. Update all HTTP caller deadlines to match the admitted document budget.

For each page, prefer usable native text, otherwise render one bounded page image
and use a dedicated local OpenAI-compatible OCR model adapter. Each request has a
fresh transcription-only context, no tools and no other pages. Tesseract remains
an explicitly recorded CPU fallback. Source bytes and completed page results are
committed before acknowledging their durable effects. Model calls happen outside
SQLite transactions. Original PDF identity, physical page index, extraction method
and output provenance accompany every page; generated OCR is not original source.

One shared file lock admits one OCR call at a time. Before dispatch, persist an
in-flight marker; clear it only after a known completed/error response is retained.
A crash, timeout, lost ownership or uncertain response leaves an uncertainty record.
A released process lock does **not** prove inference stopped: block new OCR dispatch
until backend-idle reconciliation has been recorded. Explicit retry reuses completed
pages and may retry the interrupted page only after that reconciliation; it preserves
attempt counts and prior receipts. No stale lease or process restart silently clears
uncertainty. This protects the OCR lane, not a reserved slot against other clients of
the model endpoint.

Use per-page timeouts and bounded transient retries. Derive the admitted document
deadline from actual page count and configured per-page allowance, including queue
wait and retry allowance; do not apply the existing 120-second request lifetime to
long jobs. Report admission limits explicitly instead of silently truncating pages.
Operator-defined hard safety bounds remain configurable and need capacity evidence
(ADR-0077); neither arbitrary page caps nor unbounded uploads are acceptable.

## Alternatives considered

- Whole-document multimodal requests: reject as the default; poor interruption,
  memory and provenance boundaries.
- Replace Tesseract unconditionally: reject; current simple-page tests show it is
  faster and correct, and CPU fallback remains valuable when GPU capacity is absent.
- New queue/broker/database service: defer; not needed for this single-host bounded
  extraction owner and would create another operational recovery boundary.
- Unscoped content-hash cache: reject; identical private documents from different
  callers must not expose existence, output or timing through shared result access.
- Retry a dispatched model request after a lease expires: reject; expiry does not
  prove the remote request ended and can breach the one-call GPU bound.

## Consequences

Long PDFs gain explicit coverage and page-level reuse. Recovery can resume completed
work without pretending unknown calls succeeded. SQLite is a new durable **volume
format**, not a server; backup, schema version and rollback compatibility must be
operated accordingly. An uncertain dispatch deliberately blocks OCR until reconciled;
this favors bounded load over unattended availability. CPU/native extraction can
continue where safe without falsely reporting OCR completion.

The old ten-page OCR cutoff and document-wide extraction switch are replaced only
once the new path passes acceptance gates. Keep non-PDF converters, Tesseract,
required rendering dependencies, current upload compatibility and retained evidence.
Remove only code/config/infrastructure proven unused by the new path; record that
inventory in the release evidence rather than deleting by name or age.

## Confirmation

Documentation is established before implementation. Production promotion requires
unit/contract/interruption tests, protected PR checks and a long public report test
that separately proves native-page and forced/scanned OCR coverage. See the
[runbook](../runbooks/pagewise-pdf-extraction.md). This ADR does not claim those gates
have passed or that two full 128K model contexts fit simultaneously.
