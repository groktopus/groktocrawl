# W11 isolated research-only preflight

This packet records the first successful startup of an isolated SlopSearX 0.5
MCP arm for W11. It is deployment and permission-boundary evidence, not scored
research-quality evidence.

The instance used its own Compose project, network, Valkey volume, bearer
credential, and host port. Only the research grant was enabled. Staged search,
retrieval receipts, saved searches, saved-search events, dependency dossiers,
jobs, science, and security were explicitly disabled and each denial was
probed before dispatch. Targeted sensitive-engine access was also confirmed
off. The production GroktoCrawl deployment was not changed.

The retained preflight contains no credential, endpoint, private address, or
ephemeral worker identity. Its stable configuration digest is
`9c78149ff6543a77aca6032ae252b0e64cdf96a4849a160da8df0a36214ad93a`.

The earlier `preflight-incomplete-grant-boundary.json` is retained but excluded.
It enforced the six new workflow grants while failing to verify the older jobs,
science, security, and targeted-sensitive settings. Its digest is
`093ca7c87997db2ae5ec64aa8b816d012ee6ff2f19c7310841b64e3da4c752a6`.

The first startup attempt failed closed before any search because the Compose
wrapper incorrectly interpolated its container-side token reference. That
deployment defect was corrected and regression-tested before this packet was
captured.

The before-run HTTP compatibility capture exercised nine checks: HTML root,
JSON search through GET and form POST on both `/` and `/search`, `/config`,
`/health`, `/healthz`, and invalid-pagination handling. All passed, including
the SearXNG-compatible HTTP 400 response. The packet retains response shapes,
sizes, and hashes without retaining result content, the probe query, or the
private endpoint. Its stable configuration digest is
`e4995ad273449aac341088c3789d164f433cf1d4320265019282bc4342bc5a6b`.

The no-dispatch scope-equivalence capture resolves the ordinary `general`
category to seven engines and proves that the same ordered list is accepted as
an explicit research scope. The scored A0 and A1 arms must both use this list;
neither may silently substitute intent-based routing. The capture contains no
endpoint, credential, result content, or private network address.
