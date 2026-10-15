# Narrow SlopSearX provenance adapter

Status: **implemented and live-verified**

This adapter implements the narrow W11 adoption selected by
[ADR-0082](../adr/experimental/groktocrawl-x/0082-delegate-bounded-retrieval-to-slopsearx.md). Ordinary
SlopSearX HTTP search remains the default. The adapter is internal and opt-in
under the named `retrieval-provenance-v1` profile.

## Authority boundary

GroktoCrawl continues to own the user's objective, evidence gaps, acquisition,
verification, synthesis, publication, and completion. The adapter accepts only
SlopSearX's retrieval handoff, idempotent receipt, and research-manifest
contracts. It never imports remote job status into GroktoCrawl's public artifact
model.

The retained reference has a versioned GroktoCrawl schema and contains only the
handoff contract/version, snapshot and result identities, receipt identity, and
a digest of the manifest. It sets `observations_verified` and `publishable` to
false. Later verification and publication gates cannot treat a receipt as proof
of a source claim.

## Profile and failure behavior

The profile requires `retrieval_receipts` and rejects deployments that also
grant broad jobs, research, staged-search, saved-search, or dependency-dossier
authority to this credential. The receipt workflow must report healthy before
use.

Every operation re-reads the result handoff before submitting a receipt. A
missing or expired snapshot, contract mismatch, unavailable receipt workflow,
receipt identity mismatch, or manifest omission fails visibly and returns no
reference. Receipt keys are derived from the GroktoCrawl owner, SlopSearX result
identity, and exact capture observation, so retry after timeout or restart
replays the same mutation.

The production transport opens a dedicated authenticated MCP session lazily,
serializes calls within that session, and never logs the bearer token. Enabling
the profile requires all three settings: profile name, absolute MCP endpoint,
and its dedicated token. Partial configuration fails service construction. A
profile capability failure blocks startup and appears as a degraded dependency
in aggregate health. With the settings absent, no MCP client is created.

## Remaining production slice

Migration 15 adds an internal PostgreSQL reference table. Each retained
research artifact set may own at most 100 references, each no larger than 8
KiB. A reference is replay-idempotent only when its canonical digest is
unchanged. It inherits retention ownership from the artifact set through a
foreign key with cascading deletion; it cannot outlive a deleted research
artifact set. The remote snapshot expiry is retained separately so operators
and readers can distinguish a durable audit identity from still-readable remote
state.

Disabling the profile is the rollback switch: no MCP client is created,
ordinary HTTP search remains active, and retained references remain inert audit
metadata until their owning artifact set expires or is deleted. The additive
table need not be dropped during rollback.

The isolated deployment profile is `compose.slopsearx-provenance.yml`. It
overrides every broad SlopSearX MCP grant to false, enables only retrieval
receipts, uses one dedicated bearer token for both ends of the connection, and
waits for MCP health before starting the agent. Start it explicitly with both
the override and Compose's `mcp` profile; the file is absent from ordinary
deployment commands.

The [live evidence packet](evidence/slopsearx-provenance/2026-09-19-live-adapter/)
passes receipt replay, missing and expired state, broad-grant rejection, fresh
schema-15 bootstrap, and stop/restart rollback. The exercise also retained and
fixed two defects it discovered: the wrong service-policy tool and a missing
schema-version constraint extension.

The contract and recovery behavior are based on the [W11 provenance and
recovery evidence](evidence/slopsearx-substrate/2026-09-11-isolated-provenance/)
and the final [W11 findings](slopsearx-substrate/w11-findings.md).
