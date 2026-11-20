# GroktoCrawl product roadmap

GroktoCrawl helps self-hosting researchers and agent developers acquire inspectable sources and complete cited research with understandable failure states.

Planning baseline: November 16, 2026. [Linear holds current product priorities](https://linear.app/groktopus/document/product-roadmap-now-next-later-60f2031006bf); GitHub holds implementation issues, PRs and technical contracts. This roadmap sets sequence, not delivery dates. MAG-48 now assigns bounded engineering delivery of B1/B2/B3 after the MAG-43 handoff. Assignment does not establish delivery completion or a release commitment.

## Now: reliable answers and a clear dependency contract

- **Locate ordinary answer failures.** [GRO-122](https://linear.app/groktopus/issue/GRO-122) uses bounded deterministic API/MCP fixtures to identify acquisition, generation, validation, transport or cancellation failures. Deliver an attributed reproducer and repair recommendation, or an explicit unreproduced result. Reported failures have no established root cause; do not infer a timeout cause from timing alone.
- **Make search outcomes truthful.** [GRO-123](https://linear.app/groktopus/issue/GRO-123) distinguishes valid zero matches from partial or failed acquisition and unknown provider status. Preserve caller retry policies, date-enforcement proof and cancellation. Verify public behavior and dispatch counts, including malformed responses, configuration/authentication errors and rate limiting. The source-supported classification gap is not a proven cause of answer failures.
- **Prepare and qualify the search dependency contract.** [GRO-124](https://linear.app/groktopus/issue/GRO-124) versions the Python-reference/native package, configuration, fixture and rollback matrix. Keep the pinned compatible dependency until an actual native package passes consumer acceptance through search, answer and agent routes. Include scope/engine precedence, pagination, metadata, outcome/error cases, bounded dates and optional provenance. Matrix preparation can proceed now; native execution depends on [SlopSearX runtime assembly #921](https://github.com/magnus919/SlopSearX/issues/921) and applicable supplier qualification. Consumer success does not close the whole Rust rewrite.
- **Reconcile current documentation.** Update stale roadmap, migration and ADR implementation labels against dated evidence. Keep source main, published release and running deployment distinct. Contributor/release/dependency triage continues; existing owners retain active work.

These are the adopted priorities. The recorded Linear Backlog/unassigned state is a historical snapshot; MAG-48 has since assigned the bounded batch to engineering. B3a documentation and B3b matrix preparation complete separately; neither closes native qualification or all of GRO-124. B1 is not a hard prerequisite for independent compatible B2 work or B3 preparation. Review after diagnostic or acceptance evidence changes and at the monthly roadmap review.

## Next: usable outputs and retained-evidence workflows

- **Machine-readable scrape links.** [GRO-125](https://linear.app/groktopus/issue/GRO-125): define a bounded separate collection through scraper/API/MCP, or explicitly reject unsupported format requests. Cover links-only/combined formats, base and relative URLs, schemes, fragments, deduplication, empty pages and cache behavior.
- **Usable browser content retrieval.** [GRO-126](https://linear.app/groktopus/issue/GRO-126): provide a compatible bounded/paged reading flow, or explicit metadata semantics plus a supported content action. Verify limits, continuation, empty/large pages, session ownership and expiry.
- **Retained-evidence synthesis recovery and fidelity.** [GRO-127](https://linear.app/groktopus/issue/GRO-127) qualifies existing [PR #728](https://github.com/groktopus/groktocrawl/pull/728) with its current owner. Verify manifests, digests, assigned citations, validated assembly, concurrent scoped resume and response-checkpoint reuse without search/scrape replay. Unknown provider outcomes require explicit retry; refusal/context failures stay terminal. Measure fidelity and omissions against frozen sources separately from recovery/assembly and 132-source/386-chunk assignment mechanics; no paid evaluation starts from this roadmap.

Links and browser-content work have no established Rust migration blocker. Next items remain provisional until capacity, contract and acceptance are agreed. User-value expansion depends on observed task usefulness; no retention or answer-quality uplift is asserted.

## Delivered foundations and verification residuals

- [Version 1.0.1](https://github.com/groktopus/groktocrawl/releases/tag/v1.0.1) completed the recorded integration cleanup. See the [cleanup plan](plans/release-1.0.1-cleanup.md) and [verification report](experiments/release-1.0.1-verification.md). Historical evidence is not a fresh deployment attestation. The original strict approximate-search comparison failed in 28 of 100 frozen-query cases and remains failed evidence. A separate exact-search check passed at unchanged 0.0001 tolerance; it does not establish runtime approximate parity or general relevance.
- [ADR-0072](adr/0072-valkey-leased-legacy-job-recovery.md) governs bounded legacy recovery: queued/unclaimed redispatch, explicit unknown outcomes for expired claimed work and persisted at-least-once webhook delivery. This is not general resumability or exactly-once execution. Separate durable research follows [ADR-0070](adr/0070-execute-live-durable-research.md).
- PDF pagewise native/Tesseract extraction merged in [#719](https://github.com/groktopus/groktocrawl/pull/719). [ADR-0079](adr/0079-extract-pdf-evidence-page-by-page.md) and the [verification plan](verification/pagewise-ocr-plan.md) require distinct exact-image/process/long-report production evidence before deployment completion is claimed.
- Provider-failure artifact preservation merged in [#727](https://github.com/groktopus/groktocrawl/pull/727), although [ADR-0083](adr/0083-classify-and-recover-legacy-provider-output-failures.md) still carries a proposed label. Reconcile approved scope and recorded acceptance rather than treating the label as proof of absent implementation. Bounded Markdown retry is distinct from durable receipts and #728 session synthesis; ambiguous paid dispatch is not silently replayed.
- Browser quarantine recovery [#723](https://github.com/groktopus/groktocrawl/pull/723), embedded lifecycle bounds [#726](https://github.com/groktopus/groktocrawl/pull/726), browser HTTP-error rejection [#732](https://github.com/groktopus/groktocrawl/pull/732) and full-page option repair [#733](https://github.com/groktopus/groktocrawl/pull/733) are merged. Existing owners retain rollout/acceptance; do not duplicate repairs or infer deployment from merge. Existing owners retain representative NIST title/date, cold/warm cache, no-HTML limitation and browser failure downstream-exclusion evidence; merge alone closes none of these user-level checks.
- Semantic retention and model-migration mechanisms already exist. [ADR-0027](adr/0027-smarter-index-retention.md) and [ADR-0028](adr/0028-embedding-model-migration-path.md) remain proposed and need backend/contract reconciliation. Model migration is partial, uses in-memory coordination and rejects PostgreSQL-only start/cutover. A supported durable full-content migration is not established merely by the endpoints.
- [Release PR #678](https://github.com/groktopus/groktocrawl/pull/678) remains an unpublished 1.1.0 proposal. Dependency work and review-runtime [#729](https://github.com/groktopus/groktocrawl/pull/729) retain existing owners and repository gates.

The enumerated roadmap and plan reconciliation is complete at the MAG-42/MAG-43 recorded snapshot. Delivery, private evidence and later changes are outside this completeness claim. Source main at that snapshot was `91e7a0ce17d14d7d234337c1d3e50b6c9db80976`; v1.0.1 and its historically verified deployed runtime are `729d34d360d819b29a633389e0067e34b2dfe7ce`. Current running images/configuration are unverified here. CTO MAG-25 retains dependency ownership; #728 retains its existing implementer. The previous domain-filter report remains withdrawn.

The [historical experimental audit](experiments/roadmap-completion-audit.md) and [ADR archive](adr/experimental/groktocrawl-x/README.md) retain their original scopes and limitations. They are not today's backlog.

## Later: options and explicit exclusions

- Contributor development skills [#394](https://github.com/groktopus/groktocrawl/issues/394) remain open, deferred behind user-facing reliability.
- Additional site adapters follow demonstrated demand and contributor intake.
- Semantic policy/model-migration redesign, stronger legacy execution guarantees and experimental research-policy promotion require a bounded evidence-backed decision.
- Multi-user/multi-tenant authentication remains outside the MVP. A managed/hosted offering remains outside this self-hosted MIT repository.

No Later item is a delivery commitment. No paid experiment, migration, production release or deployment is authorized by roadmap placement.

## How priorities change

Use [contribution intake](../CONTRIBUTING.md#contribution-intake-and-triage) with a reproducible user problem and scoped acceptance. Link existing implementation work before creating more. Continue when evidence supports the outcome; pause for missing authority, a blocking dependency or a breaking contract decision; reconsider speculative work when user value remains unproven. Review Next/Later at the quarterly portfolio review. Triage is best-effort without a response or resolution SLA.
