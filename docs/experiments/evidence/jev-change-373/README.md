# Jev page-change significance study for issue #373

This bounded shadow study is complete. It made 11 preregistered, sequential `jev-1.13.0` calls (4 calibration, 7 validation); there were no retries, live monitor changes, notifications, or deployments. The earlier six-case pilot remains separate. See [the frozen protocol](protocol.md), [case registry](case-registry.json), [pre-model references](reference-labels.json), [frozen requests](request-packets.json), [source provenance](source-snapshots.json), and [sanitized results](outcome.md), and the [casewise result ledger](casewise-outcome-ledger.json).

The fixed 0.50 cut agreed with 7/7 determinate validation reference labels (0/5 false-immaterial, 0/2 false-material; Brier 0.03739). These are best-effort assistant references, not independent gold; the small source- and intent-clustered result establishes no production accuracy or incremental answer-quality claim. Disposition is **revise; remain shadow-only**.

## Sources and publication redaction

Nine exact public revision pairs from Kubernetes website, CPython, and Sigstore Cosign are pinned to GitHub commits and raw-source hashes in `source-snapshots.json`. At the time of the freeze, full snapshots and complete diffs were retained for auditing. After the model calls, four published Cosign snapshot copies were redacted to remove unrelated upstream encrypted private-key blocks that triggered Secret Scanning. The original acquired hashes, source links, exact redaction line ranges, and published hashes are recorded in [publication-redactions.json](publication-redactions.json) and [publication-amendment.json](publication-amendment.json); line counts are preserved, and all judged excerpts remain exact at the same source line numbers. The full private-key material is not included. No evaluated excerpt overlaps a redacted span.

The Jev runner defaults to offline input validation. Its post-run validation checks the frozen input ledger, publication amendment, and exact source-to-excerpt line mapping. Raw provider receipts and the attempted/outcome journal are retained privately outside the repository.
