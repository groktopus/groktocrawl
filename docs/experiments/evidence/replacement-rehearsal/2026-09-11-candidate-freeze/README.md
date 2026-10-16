# W9 isolated candidate freeze — 2026-09-11

Status: **candidate deployment and cross-client artifact journey verified; no
incumbent traffic or state changed**

This packet freezes the first GroktoCrawl-X replacement candidate for the W9
compatibility, migration, rollback, and operational comparisons. It is an
experimental candidate, not a production cutover or a replacement for mainline.

## Frozen candidate

| Item | Frozen value |
|---|---|
| Source revision | `6b46bc9a3f44a1571ad68d5453fef90edc99466b` |
| Compose SHA-256 | `c866f7ccfb7ae47f859ce26ba396e67af3d5fb2b31a3676273d603dc3a15ac3f` |
| Python lock SHA-256 | `4c383538479287eedaf4ffde5f2044f29972897a256e07f9b57fa3d1bc42648a` |
| Candidate host | `inference.example.internal` |
| Compose project | `groktocrawl-x-candidate` |
| Verification time | `2026-09-11T16:59:54+00:00` |
| PostgreSQL research schema | `14` |
| pgvector extension | `0.8.6` |
| Semantic serving store | `pgvector` |
| Semantic rollback store | Qdrant, ready but not serving |

The candidate was built from the full source revision. Locally built images use
that revision as their tag. External PostgreSQL/pgvector, Qdrant, Valkey, and
SlopSearX images are pinned by digest in the Compose definition; the receipt
records their resolved image identities.

The SlopSearX pin is the exact image digest running in the incumbent deployment
at freeze time. This keeps the following compatibility comparison from mixing a
research-architecture change with a search-provider version change.

## Isolation evidence

Before first startup, the target returned no containers or volumes carrying the
`groktocrawl-x-candidate` Compose-project label. Startup then created only the
candidate-prefixed project, containers, networks, images, and named volumes.
The HTTP and MCP ports bind to target-host loopback. PostgreSQL, Valkey, Qdrant,
SlopSearX, scraper, browser, semantic, and portal ports are not published.

The incumbent remains in its existing deployment on `incumbent.example.internal`. The candidate
used a different host and no command in this rehearsal addressed the incumbent
checkout, Compose project, containers, networks, or volumes.

The pre-start empty-target observation was made interactively before candidate
resources existed and has no separate machine-generated receipt. The checked-in
Compose topology and the post-start receipt provide the durable isolation proof;
future clean-target rehearsals should capture the empty resource listing in the
same receipt.

## Verified journey

The verifier admitted one bounded research run and required it to complete. It
then retrieved the terminal event stream, artifact-set manifest, and exact
summary, analysis, and dossier bytes.

The same retained run was subsequently inspected through the repository CLI and
through MCP. Both clients reproduced the completed state and artifact-set ID,
and both retrieved summary bytes whose SHA-256 digest exactly matched the HTTP
retrieval. This proves one coherent artifact journey across HTTP, SSE, CLI, and
MCP rather than four unrelated liveness probes.

The same run also proved:

- all ten declared services were running and healthy;
- PostgreSQL was the retained research-artifact authority;
- schema migration 14 and the pgvector extension were active;
- pgvector was serving semantic requests while Qdrant was rollback-ready;
- Valkey, SlopSearX, scraper, browser, and portal dependencies passed aggregate
  application health;
- a real SlopSearX query returned results through HTTP and the CLI;
- MCP authentication, initialization, and research tools worked through the
  loopback-published transport.

## Evidence and reproducibility

- [`verification-receipt.json`](verification-receipt.json) is the sanitized,
  machine-readable result. It retains identifiers, hashes, service health,
  image identities, and cross-client equality checks. It excludes credentials,
  environment values, private addresses, raw Compose labels, mount paths, and
  artifact bodies.
- [`compose.experimental-candidate.yml`](../../../../../compose.experimental-candidate.yml)
  is the deployment manifest.
- [`experimental-replacement-candidate.md`](../../../../runbooks/experimental-replacement-candidate.md)
  is the operator procedure and failure boundary.
- [`verify_experimental_candidate.py`](../../../../../scripts/verify_experimental_candidate.py)
  is the executable acceptance check.

## What this does not establish

This freeze proves deployment isolation, readiness, and one coherent client
journey. It does not yet establish compatibility with the incumbent across the
full API, migration correctness, restart/restore behavior, rollback correctness,
seven-day operational stability, resource suitability, or production readiness.
Those are the remaining W9 gates in issues #309–#312.
