# Adopt the research architecture in GroktoCrawl

## Outcome

GroktoCrawl has adopted the research architecture developed in GroktoCrawl X.
Existing API, MCP, CLI, and deployment contracts remain supported. Future
implementation and issues belong in the main repository. Release, deployment,
and repository consolidation remain tracked below.

## Source baseline

- Mainline: `f7340e1d12d96a037461e5de9c969110f0cd8896`.
- Experimental: `9c7744b66f205080a44bfd89808494dbe80eb5a4`.
- Shared ancestor: `34b4975bc7baaf25510ed34029b957f95b59de70`.

Merge the histories, then reconcile the behavior. The fork alone does not include
all recent mainline improvements. Retain mainline search pagination, source
exports, acquisition coverage, token-budget controls, MCP session/plan/memory
tools, portal submission fixes, and release safeguards alongside the new code.

## Delivery steps

1. **Integrate and test.** Resolve overlapping changes, preserve both sets of
   regression tests, and run unit, service, MCP, browser, and Docker integration
   checks. Make research-storage and protected-capture checks dependencies of
   the existing required Runtime Gate. Preserve multi-architecture publishing.
2. **Document adoption.** Preserve original experimental decisions and results
   as historical evidence. Record mainline adoption in ADR-0069 without
   colliding with existing ADR numbers. Update current product documentation
   and provide an upgrade and rollback runbook.
3. **Rehearse the data upgrade.** Back up existing state and verify restoration
   in isolation. Add PostgreSQL without replacing existing volumes. Backfill
   the existing vector corpus and reconcile identifiers, counts, and content
   before changing the serving store. Keep Qdrant available for rollback.
   Follow the [migration runbook](../runbooks/mainline-architecture-migration.md),
   including its reconciliation checks and abort path. Record the rehearsal
   and cutover results before declaring completion.
4. **Release and deploy.** Merge after required checks pass and published review
   findings are addressed. Publish official images and update GroktoCrawl
   services with those images. Verify the existing live `/v2` research, document,
   and capture paths, plus MCP, CLI, and pgvector readiness. Keep scripted
   fixture-run admission disabled with
   `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false`:
   the new durable run endpoint executes an example and is not connected to
   objective-dependent live acquisition, verification, and retained
   publication. The existing four research flags may remain enabled for
   read-only access to retained records. Pgvector cutover is an independent storage change and does not
   require enabling fixture-run admission. Preserve the existing search-engine
   service and its configuration.
5. **Consolidate development.** Move remaining optional research issues to
   mainline, redirect the experimental repository to the supported project,
   and retain its history and recovery material.

## Completion checks

Completion of the mainline/vector migration requires an official mainline commit
and published images, a verified running deployment, preserved existing data and
client contracts, a tested vector rollback path, and reconciled documentation
and issue ownership. It does not make the fixture-backed run endpoint a live
research product. That requires separate product work to connect the existing
live research acquirer to the durable execution, verification, and publication
path, with its own objective-dependent HTTP/MCP and failure-recovery acceptance
tests. Passing migration tests alone does not establish that capability or
deployment completion.

Date-bounded searches remain conditional on the backend returning trustworthy
enforcement evidence. Unsupported backends must return an explicit error.
This migration does not change the search engine or claim new search-quality
results.

## Progress

- Mainline integration merged in PR #665 at `7741ddfe03773acae08e0cef47aa6ad6a457b481` after the required integration and compatibility checks passed.
- The isolated restore and shadow-backfill rehearsal matched all 11,852 records across IDs, payload/content, and vectors.
- Stable 1.0.0 was published on 2026-10-16 ([release](https://github.com/groktopus/groktocrawl/releases/tag/v1.0.0)); its release PR merged at `f2ebbbb4e8da42c96f054e19d5d86fe556d62a6e`.
- Official image publication and immutable image digests are still pending verification.
- The isolated restore and backfill verified pgvector parity; live vector-serving cutover and deployment verification are pending. Verify the existing live `/v2` research, document, capture, MCP, CLI, and semantic paths. Keep scripted fixture-run admission disabled with `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false`; the `/experimental/research/v1` run adapter is an example, not a live researcher. The existing four research flags may remain enabled for previously retained records.
- Transfer of remaining optional issues and retirement of the experimental repository are pending.

The live adapter gap is separate product work: connect objective-dependent live
source acquisition, evidence and claim verification, and retained publication to
the durable run protocol. Acceptance must exercise meaningful HTTP and MCP
objectives and verify retained source/artifact bytes, as well as budget limits,
cancellation, and recovery across a forced process crash.
