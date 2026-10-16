# W9 final checkpoint with LiteLLM `free`

Checkpoint 2 passed all 12 declared operations at `2026-09-30T19:40:57.866103Z`
on unchanged runtime `46a528228b1365189cdd38d0bcdb12109a8dc763` and model
alias `free`, from a clean matching source checkout. All eleven services were
healthy; text and structured-output readiness, pgvector serving, Qdrant rollback
readiness, and cross-client retained artifacts passed.

The first attempt failed at `/v2/agent` with HTTP 503 after a model health-check
timeout. [Its sanitized failure receipt](first-attempt-failure.json) preserves
original-source provenance and earns zero credit. The single protocol-permitted
retry passed. Both original attempt packets remain retained privately.

The window now proves **36 successful operations and 3/3 checkpoints**, after
more than seven elapsed days. [Closeout verification](closeout.json) passed with
no errors over all three current-window packets and expected model `free`.
The [checkpoint receipt](checkpoint.json) hashes sanitized compatibility,
research, and resource receipts; original research/resource digests remain
available in the normalized receipts. No deployment change was made.

See the [readiness decision](../../../w9-replacement-readiness-decision.md) for
limits, follow-ups, and the subsequently completed owner architecture ratification.
