# Adopt the research architecture in GroktoCrawl

## Outcome

GroktoCrawl will include the research architecture developed in GroktoCrawl X.
Existing API, MCP, CLI, and deployment contracts remain supported. Future
implementation and issues belong in the main repository. The experimental
repository remains a historical record once migration is verified.

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
   findings are addressed. Publish official images, update GroktoCrawl services
   with those images, and verify API, MCP, CLI, stored research, and recovery.
   Preserve the existing search-engine service and its configuration.
5. **Consolidate development.** Move remaining optional research issues to
   mainline, redirect the experimental repository to the supported project,
   and retain its history and recovery material.

## Completion checks

Completion requires an official mainline commit and published images, a verified
running deployment, preserved existing data and client contracts, a tested
recovery path, and reconciled documentation and issue ownership. Passing tests
alone does not establish deployment completion.

Date-bounded searches remain conditional on the backend returning trustworthy
enforcement evidence. Unsupported backends must return an explicit error.
This migration does not change the search engine or claim new search-quality
results.

## Progress

- Both source baselines fetched and compared.
- Mainline-only runtime, documentation, and deployment contracts audited.
- Integration and compatibility checks passed on the prior integration head; final startup and health-check patch verification is in progress.
- Isolated restoration and shadow-backfill rehearsal completed: all 11,852 vector records matched across IDs, payload/content, and vectors.
- Release, live deployment verification, and repository consolidation remain pending.
