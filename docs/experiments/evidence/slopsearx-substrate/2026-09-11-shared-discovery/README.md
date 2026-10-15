# SlopSearX 0.5 shared-deployment discovery

Status: **preflight development evidence; excluded from W11 scoring**

This packet records a read-only validation of the W11 preflight client against
the newly installed SlopSearX 0.5 service. The shared deployment is not the
isolated W11 measurement environment.

The preflight confirmed:

- package version 0.5.0 and the declared source and image identities;
- 35 discoverable MCP tools, including all tools required by the draft W11
  protocol;
- durable leased research execution with connected Valkey and snapshots;
- the expected enabled research grant; and
- `tool_disabled` outcomes for dependency dossiers, retrieval receipts, saved
  searches, saved-search events, and staged search.

The redacted [preflight record](preflight.json) has configuration digest
`de343af62cb56eb4f6b0f2bcf32467269c058cae453ea23f4624bba7444cc167`.
It contains no endpoint, bearer token, or private network address. The digest
excludes capture time and ephemeral worker identity.

This packet proves that the client can observe the installed contract and that
disabled grants fail closed. It does not prove research quality, isolated
tenant behavior, recovery, SearXNG compatibility, or suitability for adoption.
Those remain W11 measurement gates.
