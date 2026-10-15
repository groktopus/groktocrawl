# W12 narrow SlopSearX provenance adapter

Status: **passed**

The isolated live exercise used the W11-tested SlopSearX image with a dedicated
credential granting retrieval receipts only. Ordinary HTTP search remained the
default and the frozen W9 candidate was not changed.

The adapter accepted the receipt-only profile, read a versioned retrieval
handoff, submitted and replayed one identical capture observation, and retained
stable snapshot, result, receipt, and manifest identities. The replay returned
the same receipt identity. The internal reference explicitly remained
unverified and unpublishable.

The failure checks also passed:

- a still-running W11 service carrying the broader research grant was rejected;
- a missing snapshot returned no reference;
- a one-second snapshot was allowed to expire and then returned no reference;
- the first positive-path attempt exposed an incorrect capability-tool choice
  in the adapter, which was fixed to use the service-status contract and
  covered by a regression test.

A fresh ephemeral PostgreSQL instance applied migrations 001 through 015. It
reported schema 15 and the bounded provenance-reference table. The initial
bootstrap found that migration 15 had not extended the schema-version check
constraint; the migration was corrected and the complete bootstrap then
passed.

For rollback, the isolated MCP service was stopped. The ordinary candidate
agent, ordinary SlopSearX HTTP service, and isolated state store all remained
healthy. Restarting the MCP service restored health. No retained table was
dropped and no mainline or W9 candidate routing changed.
