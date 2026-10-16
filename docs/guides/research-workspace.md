# Retained research workspace

The experimental workspace browses existing independent roots. It does not create
chat history or replay research implicitly. Capabilities report the real fixture,
process-local, Valkey, or PostgreSQL boundary; existing feature gates still apply.

Use `groktocrawl research workspace --json` to list permitted roots and
`groktocrawl research workspace RUN_ID --json` to inspect one. Read its revision,
audited manifest, citation references, coverage, state, retention information and
named actions. Listing accepts bounded `--limit` (1–100), `--offset` and
`--run-offset`; follow `next_offset` and `next_run_offset` independently when
PostgreSQL artifact roots and temporary execution records coexist. The MCP equivalents
are `research_workspace` and `research_resume`.

Explicitly resume with `groktocrawl research resume RUN_ID render
--expected-revision DIGEST --layer summary --json`. Operations are render, export,
request_evidence, attach, cancel, delete, documents, document_evidence and
followup_preview. Evidence requires `--snapshot-id`;
attachment requires `--session-id` and `--expected-session-revision`. Completed
roots require the observed research revision. API actions are
`POST /experimental/research/v1/workspace/{run_id}/actions`; selection is
`GET /experimental/research/v1/workspace/{run_id}`. A stale revision returns 409,
foreign identity 404, and known deleted/expired root 410. Rendering/export returns
exact Markdown and its digest in JSON; existing `research download ARTIFACT_ID`
remains available for raw file export.

The portal's `/workspace` lists and selects roots, shows exact audited report text
and cited bytes, and offers explicit attachment/cancellation/deletion. Reload
selects the opaque ID in the URL fragment, then rechecks server permission and
liveness. It stores no private evidence in localStorage. API credentials must be
supplied by the caller's existing deployment authentication; no credentials are
created or stored by the workspace.

Document context lists and reads actual owned session document refs through named
`documents`/`document_evidence` actions. Upload and document queries remain on the
existing document/session APIs. Experimental root attachment records only an
attachment; it does not copy root sources into session query evidence.

Follow-up previews use `/v2/followup/preview` with selected identities and do not
execute research. Each follow-up remains an independent root. Pending, failed and
cancelled runs expose their true state; partial/insufficient completed coverage is
reported as supplied by the audited manifest, never called exhaustive.

For PostgreSQL authority, schema 16 extends existing authority with exact source
bytes and audited knowledge. Operators must run the normal ordered migration
procedure through `SlopSearXProvenanceStore.migrate()` (14→15), then
`ArtifactAuthority.migrate_evidence_authority()` (15→16) in the authorized isolated
environment. This change does not run migrations automatically. Admissions fail
503 before starting work if schema 16 is absent. Old report-only records remain
readable but cannot invent citation recovery. New bodies share the same 30-day
artifact expiry and tombstone; no new store or indefinite archive is created.
Scoped PostgreSQL root listing and evidence remain available after the shorter
Valkey execution history expires. Failed/pending roots with no published artifact
set expire with their execution ledger. Deleting an unpublished root also fences
late publication; deleting a catalog-restored root removes its retained sources.
