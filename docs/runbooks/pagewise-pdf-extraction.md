# Pagewise PDF Extraction: Rollout and Recovery

This is the rollout contract for [ADR-0079](../adr/0079-extract-pdf-evidence-page-by-page.md).
Implementation-specific commands and measured evidence must be added by the release
PR before promotion; this page does not imply unimplemented controls are available.

## Deployment requirements

- One parse-service deployment on a host, with a persistent local checkpoint volume
  shared by its processes. Keep SQLite, original bytes, atomic page records and lock
  files on that volume. Do not place lock/SQLite files on a storage backend whose
  POSIX locking/durability semantics have not been verified.
- Use a dedicated explicit local OCR endpoint/model/revision, secret-mounted credential
  and versioned prompt/render policy. Never copy credentials or private hostnames into
  public docs/evidence. No generic model-router fallback to an external provider.
- Set the OCR lane to one in-flight request initially. This is an OCR admission bound,
  not isolation from other clients of the shared model server. Tune only with measured
  shared-load evidence, not the model's configured sequence count alone.
- Validate page count, original byte size and bounded render pixels before dispatch.
  Configure per-page attempts/timeouts and page-proportional document budget; expose
  any rejected admission or partial coverage. Back up ledger plus source artifacts
  consistently before a schema migration; keep migrations additive/versioned.

## Promotion evidence

1. Run targeted unit, integration and failure-boundary suites; include existing parse,
   upload, session/source digest, webhook and authorization regressions.
2. Build candidate images and identify exact commit/image digest. Inspect Compose and
   confirm persistent storage is mounted, model health is good and no secrets are in
   public changes. Keep prior image/config and checkpoint schema rollback compatibility.
3. Deploy through protected merged revisions, not a dirty local checkout. Start with a
   synthetic mixed PDF proving per-page selection, page 11+ and exact table numbers.
4. Fetch a long public annual report from its publisher. Retain URL, retrieval time,
   SHA-256 and physical page count. Run normal extraction and report native versus OCR
   page counts. Rasterize selected representative pages into a separate image-only test
   PDF for real OCR coverage; record that transformation and do not call it the original.
5. Check first/middle/final page evidence, table values, ordering, progress, latency,
   token/memory pressure, interruption/retry and shared-model responsiveness. Never
   infer full OCR success from a native-text-only annual report run.
6. Inventory old code/config removed and justify each removal. Preserve the CPU fallback,
   converters, source retention, current working uploads and unrelated infrastructure.

## Interruption and rollback

An interrupted execution retains completed page checkpoints. A dispatched page without a
known result and its global uncertainty marker require reconciliation: establish
backend idle through trusted operator evidence (or restart it deliberately within
approved operational scope), record the decision, then explicitly retry. Do not clear
an uncertainty marker merely because a file lock/lease is no longer held. Retry does
not erase attempt history, source identity, prior warnings or previously stored outputs.

For repeated failures, stop new OCR admission, retain the ledger/source volume, and
restore the previous parse image/config. Do not delete checkpoints or downgrade an
incompatible ledger. Native/Tesseract fallback can be used only with truthful method
and coverage metadata. A rollback of parse-svc does not require disabling the shared
model's vision capability or changing other model consumers.

Observe queue wait, active OCR count, completed/failed/skipped pages, engine/fallback
counts, retry/unknown dispatches, page latency, output-token exhaustion and total execution
age. Label metrics with bounded categories, not source URLs, filenames or private text.
