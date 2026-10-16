# W11 isolated provenance evidence

This directory contains the secret-free evidence from the first live
SlopSearX-to-GroktoCrawl provenance case.

- `preflight.json` proves that the isolated service used the pinned SlopSearX
  0.5.0 image, enabled only retrieval receipts, denied the other eight
  specialist grants, connected to its private Valkey store, and exposed the
  expected 35-tool contract.
- `live-chain.json` records three discovered results. All three retained stable
  discovery identity, an attributed receipt, idempotent replay, and complete
  manifest linkage. Two pages were captured and hashed. One eligible page
  returned an HTTP error and was retained as a failed capture.

The hard gate in `live-chain.json` concerns provenance integrity. It passes
when both successful and failed retrieval observations remain correctly linked
and are explicitly described as observations rather than verification. It is
not an acquisition-success or research-quality score.

The query, URLs, result content, captured markdown, receipts, and credentials
remain in the restricted experiment directory. The public records contain
only hashes, counts, states, bounded service metadata, and gate outcomes.

The first Compose start attempted the deployment's exported default MCP port
instead of the arm's private port and was excluded before the MCP service
started or any search ran. Recreating the MCP service without that inherited
shell value applied the arm manifest's port and produced this evidence.
