# Explicit contextual follow-ups

`POST /v2/followup/preview` returns a provider-free, read-only standalone query
proposal. It never launches retrieval, synthesis, session steps or suggested
work. Independent research roots remain the default (ADR-0084).

```json
{
  "wording": "How does it handle cancellation?",
  "selected": [{
    "kind": "session",
    "container_id": "session-id",
    "ref_id": "ref-id",
    "subject": "Alpha crawler"
  }],
  "max_age_seconds": 86400
}
```

`subject` is the caller's explicit name, not an inferred entity or factual claim.
Identity is the full `(kind, container_id, ref_id)` tuple. Session refs must exist
in that exact live session. Credential-owned sessions reject foreign credentials;
legacy unowned session IDs retain their existing opaque bearer capability model.
Root choices use the experimental evidence API's credential scope, feature gates
and deletion checks. Unsupported or unavailable evidence returns an error rather
than a plausible context guess. Root snapshot timestamps unavailable through that
API produce `unknown` temporal status. No claim of current truth follows from
`recent_snapshot`; older or future-dated snapshots are `historical`.

With exactly one selected subject, `it`/`its` can be expanded deterministically.
Plural pronouns, demonstratives and multiple referents return
`needs_clarification`, no proposed query and no actions. Selection order never
assigns “the other one.” Supply `standalone_override` to explicitly name each
referent; the original wording is always returned. `correction` is preserved but
requires an override before a ready proposal. An unrelated fully named question
stays unchanged; selected context does not silently enter its prompt.

The response includes the exact selected identities, temporal metadata and typed
`compare`, `deepen`, `narrow`, `request_evidence` suggestions as appropriate. Their
query and identities are inspectable parameters, not executable URLs or tool
calls. Every action requires confirmation. To execute, explicitly submit the
chosen query/refs to the existing research or session APIs, which validate
liveness again. A preview is not a lease on evidence and creates no retained data.

Context is bounded to eight selected identities, 160-character subject names,
4,000-character wording/override and a 1,000-character correction. Evidence bodies
and accumulated histories are not used to formulate the proposal; source prompt
injection cannot control a model because no model is called. Selection validates
existing refs rather than fetching remote URLs. Validation has a 15-second deadline. Normal request cancellation
propagates through the async reads; no background task is created.

CLI: `groktocrawl followup @request.json --json` (or inline JSON). `--dry-run`
previews the HTTP request only. MCP: `followup_preview(request={...})` accepts the
same request contract and returns the same proposal. This preview adds no UI;
the persistent workspace consumes the API separately.
