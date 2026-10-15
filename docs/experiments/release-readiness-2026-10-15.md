# GroktoCrawl X release check — October 15, 2026

## What changed

[PR #457](https://github.com/magnus919/groktocrawl-x/pull/457) fixes three
problems found during release checks:

- Publication-date bounds must survive API, MCP and CLI wrappers. Clients check
  enforcement receipts and returned dates. Unsupported requests return an error
  instead of silently returning results outside the requested dates (#456).
- Focused research stays within one acquisition pass. Gap detection previously
  could enable a second pass despite that setting (#459).
- Streaming `agent --json` returns one valid JSON object, including sources.
  Stream failures retain structured errors and a nonzero exit status (#460).

The tested application source is
`071b8ada7de9f67ba4746c7c5d5898c01de8a580`, merged as
`b3293f4986fe76fbe518563039a3d5a2282886bf` with the same source tree. No
database migrations are included.

## Verification

- Both required gates passed on the exact tested head: Code Quality Gate and
  [Runtime Gate](https://github.com/magnus919/groktocrawl-x/actions/runs/38805102725).
  The full integration run recorded **2,433 passed, 212 skipped and 17
  deselected** cases. Protected capture, browser, FlareSolverr and PostgreSQL
  storage jobs also passed; their results are separate from that count.
- CLI regression suites passed 122 tests; the standalone MCP suite passed 256;
  the focused/deep research regression slice passed 24. New regression cases
  failed before the focused-pass and streaming-JSON fixes.
- Existing PostgreSQL schema-16 and Valkey snapshots were restored into a
  disposable four-service project. Both prior and new application versions
  read retained workspaces and documents with matching revisions and content
  digests. Application rollback and roll-forward passed on that restored data.
  This is a tested restore/read-compatibility result, not a claim that new
  migrations were exercised: this release has no migrations.
- Live MCP initialization, tool discovery, capability invocation and a search
  returning three results passed. Focused research using a supplied public
  documentation URL returned valid JSON with sources through the `free` model
  alias. Structured extraction against that source also passed.

- The experimental API and MCP services now run the exact tested application
  revision. All 11 services are healthy, the model alias remains `free`, and no
  active jobs remain after verification. No dependency container was replaced
  or restarted; published ports are unchanged.
- Seventeen post-update functional checks passed, including credential
  rejection, MCP initialization and tool use, retained workspace revision and
  artifact digest checks, complete document text beyond 65,000 characters,
  matching render/export bytes and stale-revision rejection.
- The CLI was replaced atomically. Source and installed files are byte-identical
  and pass syntax validation. The installed executable returned capabilities
  and a real model-backed focused answer as valid JSON with sources.
- Hermes successfully called `research_capabilities` through its existing MCP
  configuration and exited successfully. Its configuration and environment
  files are unchanged. An earlier three-turn check completed the tool call but
  exhausted the test's turn limit; the five-turn confirmation passed.
- Mainline API and search containers remained healthy with unchanged identities
  and start times. The disposable restore-validation project was removed;
  private backups, prior images and recovery instructions were retained.

The [sanitized result receipt](release-readiness-2026-10-15.json) records these
outcomes without research content or deployment identifiers.

The first update was automatically rolled back after a deployment validation
check failed. The prior applications returned healthy, with unchanged dependency
containers and ports. The validation was corrected and the next update passed.
The failed attempt remains in the private operational record.

## Remaining limitation

The current search backend did not supply the enforcement proof required for
publication-date bounds. The live bounded-date CLI test therefore returned the
expected explicit error. Date-bounded successful searches remain unavailable
with that backend; ordinary search continues to work. This release fixes the
silent-loss bug without changing the search engine.

The original architecture roadmap is complete. Issues #444–448 are optional
research studies, separate from these release fixes. These functional checks
support using the candidate for the existing workload; they do not establish
better answers for every research question or a general availability guarantee.

GroktoCrawl X remains an experimental fork, separate from mainline GroktoCrawl.
Search-engine code, configuration and deployment were unchanged. Mainline
and Hermes configuration were also unchanged. Private deployment details,
credentials and research content are excluded from this record.

See the [candidate upgrade and rollback instructions](../runbooks/experimental-replacement-candidate.md#updating-an-existing-candidate).
