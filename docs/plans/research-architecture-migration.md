# Adopt the research architecture in GroktoCrawl

## Outcome

The 1.0.0 merge completed repository integration but missed live durable research
and other workflow connections. The [1.0.1 cleanup plan](release-1.0.1-cleanup.md)
now governs completion and release readiness. The steps below retain the original
migration scope; they are not evidence that deployment has completed.

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
   `FEATURE_EXPERIMENTAL_RESEARCH_FIXTURE_RUNS=false`. The 1.0.1 correction
   connects the durable endpoint to live acquisition and retained publication;
   admission requires all four research flags, schema 16, configured dependencies,
   and passing acceptance checks described in ADR-0070. Verify the deployed
   artifact before enabling it. Pgvector cutover is an independent storage change.
   Preserve the existing search-engine service and its configuration.
5. **Consolidate development.** Move remaining optional research issues to
   mainline, redirect the experimental repository to the supported project,
   and retain its history and recovery material.

## Completion checks

Completion of the mainline/vector migration requires an official mainline commit
and published images, a verified running deployment, preserved existing data and
client contracts, a tested vector rollback path, and reconciled documentation
and issue ownership. Storage migration alone does not prove live research.
The 1.0.1 cleanup additionally requires objective-dependent HTTP/MCP behavior,
retained-byte verification, cancellation and recovery tests, and verification of
the final deployed artifact. See the cleanup plan for the complete checklist.

Date-bounded searches remain conditional on the backend returning trustworthy
enforcement evidence. Unsupported backends must return an explicit error.
This migration does not change the search engine or claim new search-quality
results.

## Progress

- Mainline integration merged in PR #665 at `7741ddfe03773acae08e0cef47aa6ad6a457b481` after the required integration and compatibility checks passed.
- The isolated restore and shadow-backfill rehearsal matched all 11,852 records across IDs, payload/content, and vectors.
- Version 1.0.0 was initially published on 2026-10-16 ([release](https://github.com/groktopus/groktocrawl/releases/tag/v1.0.0)); its release PR merged at `f2ebbbb4e8da42c96f054e19d5d86fe556d62a6e`.
- Version 1.0.0 was subsequently marked prerelease after the missing live adapter was found. Do not use its publication as evidence that the cleanup is complete.
- Final 1.0.1 image publication, immutable digests and deployment verification remain pending.
- The isolated restore and backfill verified pgvector parity; live vector-serving cutover and deployment verification remain pending. Keep demonstration admission disabled. Enable the corrected live research path only after the 1.0.1 acceptance and deployment checks pass.
- Transfer of remaining optional issues and retirement of the experimental repository are pending.

Live durable research, workspace integration, storage and restart recovery are
tracked together in the 1.0.1 cleanup plan. Controlled service tests establish
integration behavior; they do not establish improved real-world answer quality.
