# Pagewise Local OCR Verification Plan

Revised 2026-11-11 before CPU-only runtime edits. This replaces the earlier vision-model
verification plan; it does not claim implementation or production gates have passed.

## Outcome and boundaries

Extract long PDFs page by page through native text or local Tesseract, retaining scoped
source/page evidence and truthful coverage. Re-submission reuses completed receipts;
there is no background job API or automatic restart recovery. Architecture precedes
code; protected PRs and a verified production long-report test finish the release.

Tests cover domain selection/budgets, service/public contracts, real SQLite/locks and
local process interruption, then production. Mocks cannot establish OCR accuracy, OS
child cleanup or cross-process exclusion. A native-only report cannot prove OCR.

## Blocking acceptance matrix

| ID | Invariant | Evidence |
|---|---|---|
| P01 | Per-page selection and ordered complete accounting | Mixed fixture of at least 12 pages, sparse native/image/blank pages, unique sentinels through last page. |
| P02 | No false completeness after limits/errors | Inject timeout, page admission refusal, tool error, output bound and cancellation; counts reconcile and incomplete results stay partial/failed. |
| P03 | One bounded raster page per OCR operation | Assert render dimensions/pixel bound and per-page calls; no full-document image materialization. Document instructions remain data. |
| P04 | Native text remains cheap; sparse evidence is preserved | Digital fixture makes zero Tesseract calls. OCR failure retains available sparse native text with degraded status. Native/OCR are not duplicate evidence. |
| P05 | One global same-host local OCR lane survives parent failure | Two independent processes with a blocking actual child show max active=1. Kill parent/cancel/timeout; lock-owning worker retains capacity until child group killed/reaped. No orphan overlaps replacement work. |
| P06 | Durable scoped checkpoints | Restart after page N; identical resubmission does not repeat completed pages. Scope, source, engine, render or policy changes prevent invalid reuse. |
| P07 | Bounded failures and retries | Attempts remain preserved through re-submission, exhausted pages do not reset implicitly, local timeout/process cleanup tested. No remote unknown-dispatch/reconciliation dependency remains. |
| P08 | Original/staged bytes survive incomplete extraction | Crash windows before/after page commit and upload finalization; complete success alone consumes staged source. Exact digest/filename preserved. |
| P09 | Disconnect/session deletion respects authority | Interrupt during native/render/OCR/pre-admission; no late result recreates deleted session, duplicate same-document work or false delivery claim. |
| P10 | Exact citations/provenance | File SHA and extraction digest remain distinct; physical page indexes and Unicode spans resolve. Record only measured engine confidence/coordinates. |
| P11 | Sync bounded resubmission contract | API, CLI, MCP, sessions and proxies align document/transport deadlines; no polling/webhook/job claims or silent HTTP retry budget reset. |
| P12 | Existing behavior remains compatible | Non-PDF, explicit hosted opt-in, sessions, upload/auth, snapshot/digest and document journeys pass. Query/form conflict behavior tested. |
| P13 | No model path in local pipeline | Reference/config/import inventory shows model adapter, HTTP worker, model-key config and reconciliation code removed. Local failures cannot call general/free/hosted fallback. |
| P14 | Safe cleanup/storage | Removal inventory and rollback verified; retained sources/checkpoints untouched. TTL/cap cleanup skips active work, rejects exhausted capacity, and never reuses incompatible old policy receipts. |
| P15 | Optional tables are page-bounded | Camelot opt-in processes one page under local timeout/output limits; default off. Parent/child cleanup follows same bounded-process guarantees. |

The local OCR worker must own the lane lock across its child's entire lifetime. A
kernel-lock mock or killing only the HTTP request is insufficient. After interruption,
a page may retain an attempted/incomplete receipt; explicit retry counts that attempt.
No remote backend-idle evidence is needed because all local process lifetimes are
owned, bounded, terminated and reaped. Verify the actual code proves that premise.

## Production acceptance

- Use a long official public annual report (DORA is suitable). Record publisher URL,
  retrieval time, bytes, SHA-256 and physical page count.
- Run public supported parsing and reconcile every page. Inspect first/middle/final
  page evidence and numeric tables; report native versus Tesseract counts.
- Produce a labeled raster-only derivative of selected real report pages with original
  page mapping. Compare Tesseract output against visible text/numbers, not an LLM summary.
- Repeat extraction and confirm completed receipt reuse. Exercise controlled interrupted
  work without touching unrelated services; prove no surviving child breaks lane bound.
- Record exact merged/deployed image revision, health, CPU/RAM/latency, coverage and
  rollback. Existing vLLM and other consumers remain unchanged.

## Readiness

Final exact-head lint, type, unit/service/container checks and independent review are
required. Docker runs only on Linux/CI, not the Mac. Map P01–P15 to tests or observed
runtime evidence. Material post-review code changes invalidate that verdict. Failed
production gates require rollback or an explicit blocked result, not a completion claim.

Earlier docs commit `4f41a1ab` (cherry-pick `b99397b8`) passed its original design gate.
That historical verdict does not approve the revised CPU-only runtime. Re-review this
contract before code and then review the final implementation separately.
