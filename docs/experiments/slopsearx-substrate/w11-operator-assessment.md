# W11 operator assessment

Status: **complete — broad workflow rejected; narrow contracts remain viable**

This assessment asks what SlopSearX's optional research substrate adds to the
GroktoCrawl X operating model. It is limited to the experimental fork. It does
not recommend adoption before the matched quality comparison.

## Observed topology

The candidate adds one optional `slopsearx-mcp` container. It uses the same pinned
SlopSearX image as ordinary HTTP search and the existing Valkey service. It does
not add another database, vector store, model service, or durable volume. The
ordinary `slopsearx` HTTP container remains the SearXNG-compatible control.

The companion is behind the `mcp` Compose profile. A normal no-profile startup
does not create it. Enabling the profile requires a non-empty authentication token;
specialist grants remain disabled individually unless the operator enables them.
Its healthcheck performs an authenticated MCP initialization after Valkey becomes
healthy.

| Operational dimension | Observed state | Implication |
|---|---|---|
| Added services | One optional companion container | Small topology increase; another health and upgrade surface remains |
| Added state | Research workflow records share the existing Valkey service | No new datastore, but retention, backup, capacity, and key isolation must cover this state |
| Configuration | Authentication, published port, nine specialist grants, and targeted-sensitive access are explicit | Least privilege is possible; broad manual grant combinations are error-prone without named profiles or generated arm configuration |
| Image lifecycle | HTTP and MCP services use one pinned image digest | Upgrade and rollback can move together, while two running containers still need compatibility checks |
| Readiness | MCP startup waits for healthy Valkey and probes authenticated initialization | Readiness is stronger than process liveness; it does not prove every granted workflow succeeds |
| Network exposure | The experimental model publishes the MCP port | A future retained integration should prefer an internal-only service path unless direct external clients are a product requirement |

## Failures encountered

The experiments exposed operator risks that a final design must address:

1. Compose initially expanded a container-side token reference on the host. The
   MCP service failed closed and restarted. Escaping was corrected and covered by
   a rendered-configuration test.
2. Two isolated runs selected an occupied host port. Both stopped before useful
   work. The arm preparer now rejects within-arm collisions, but it cannot reserve
   a host port against unrelated processes.
3. Two recovery attempts targeted the SlopSearX HTTP port instead of GroktoCrawl's
   capture API. They were excluded. This supports using service discovery and named
   endpoints instead of manually copied ports.
4. The saved-search evaluator assumed oldest-first report order while the API
   returns newest-first. The retained data allowed correction without another
   search. Client contracts should state ordering explicitly.

These failures did not leak credentials or enter scored evidence. They still count
as evidence of configuration and integration burden.

## Recovery and evidence handling

The isolated recovery cases passed the declared interruption boundaries. Work
remained readable after interruption, deterministic replay did not repeat a search,
and receipt submission remained idempotent. A downstream page request with an
ambiguous outcome was attempted again and remained visible rather than being
silently treated as success.

Public W11 evidence currently occupies less than 100 KiB. The scored comparison
enforces a 5 MiB limit for each public and private trial artifact, with a 720 MiB
overall ceiling. This is an experiment bound, not a production capacity forecast.

## Ownership and rollback

GroktoCrawl continues to own the user's objective, evidence gaps, proposed queries,
admission, synthesis, verification, and publication. SlopSearX owns bounded search
execution, attempt accounting, snapshots, and retrieval provenance only when the
caller selects the experimental path.

The proposed rollback is operationally narrow: stop admitting recorded-continuation
work, account for outstanding jobs, switch new requests to ordinary HTTP search,
and explicitly stop `slopsearx-mcp` in the intended Compose project. Removing a
profile from a later invocation does not stop an already-running container. Keep
the companion out of subsequent startup commands; do not disable other MCP services
that happen to share the profile. Verify ordinary HTTP search and the GroktoCrawl
client journey after the switch. This complete rollback sequence has not yet been
rehearsed and remains an adoption gate.

Ordinary HTTP search remains available and no
GroktoCrawl artifact schema or PostgreSQL data must be migrated back. Retained
SlopSearX workflow records may be kept for audit or expired under their declared
policy; rollback must not require deleting them.

## Final judgment

The substrate has a plausible operating shape because it reuses the existing image
and Valkey rather than adding another stateful system. The strongest current value
is durable accounting, provenance, and bounded recovery. The weakest point is the
number of grants and endpoint details an operator can combine incorrectly.

Broad adoption is not justified. The general comparison preserved identical
retrieval results but failed the caller-authority gate in every recorded run:
the SlopSearX job became terminal on result-budget exhaustion before GroktoCrawl
declared completion. The reviewer diagnostic was too incomplete to estimate
quality and is excluded from the decision.

The operating shape remains acceptable for narrow, opt-in contracts. Receipts,
manifests, recovery, saved-search events, and dependency dossiers passed their
bounded evidence gates and add no datastore beyond the existing Valkey. Each
retained capability still needs a named least-privilege profile, an internal
service path, a documented retention owner, and a tested rollback. The complete
MCP research workflow must remain disabled by default.

## Sources

- [W11 frozen-protocol candidate](w11-protocol.md)
- [W11 research log](w11-research-log.md)
- [Isolated research-only deployment](../evidence/slopsearx-substrate/2026-09-11-isolated-research-only/README.md)
- [Recovery evidence](../evidence/slopsearx-substrate/2026-09-11-isolated-recovery/README.md)
- [Saved-search evidence](../evidence/slopsearx-substrate/2026-09-11-isolated-saved-search/README.md)
- [Composition evidence](../evidence/slopsearx-substrate/2026-09-11-isolated-composition/README.md)
- Repository `docker-compose.yml` and `.env.sample` at this branch revision
