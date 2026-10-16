# GroktoCrawl 1.0.1 verification

This report separates implementation checks, deployment checks, and demonstrated
user behavior. Controlled test responses are identified explicitly.

## Verified implementation

Implementation PRs #670–#674 and documentation PR #675 were merged before release
commit `729d34d360d819b29a633389e0067e34b2dfe7ce`. Both integration suites in
[the final image workflow](https://github.com/groktopus/groktocrawl/actions/runs/38893246002)
passed 2,798 tests, with 162 skips and 17 deselections recorded separately.
All ten multi-architecture image builds and required gates passed.

The nine durable-research HTTP cases use actual PostgreSQL and Valkey with
controlled search, capture, model, and callback responses. They verify objective
differences, retained evidence, recovery, callbacks, evidence dates, and follow-ups.
They do not measure live search or answer quality.

## Cleanup checklist

Each row in the [cleanup plan](../plans/release-1.0.1-cleanup.md) has implementation
and test evidence. Deployment status is recorded separately below.

| Requirement | Evidence |
| --- | --- |
| Live durable research | PR #670; nine HTTP adapter acceptance cases exercise different objectives, retained bytes and recovery |
| Research breadth | PR #674; controlled investigations plan multiple queries and use evidence beyond the third source |
| Callbacks and time settings | PRs #673–#674; persisted terminal delivery, duplicate handling, failure callbacks and `as_of` acceptance |
| Workspace follow-up | PR #671; selected retained evidence drives a separate investigation through API, CLI and MCP |
| Research attachments | PR #671; scoped attachment, session question, exact citation reads and deletion checks |
| PostgreSQL-only operation | PR #672; indexing, memory operations and restart without Qdrant, plus deployment migration checks below |
| Existing background jobs | PR #673; queued recovery, competing ownership, cancellation and honest unknown outcomes across six job families |
| Document workflow | PR #671; real Parse and Valkey with controlled answering through API, CLI and MCP; deployed demonstration below |
| Evidence and capture | PR #671; late-source passage selection and automatic recovery through the public scrape route |
| Documentation | PR #675 and the final documentation audit below |

## Documents and clients

The audit covered README, AGENTS.md, CONTRIBUTING.md, architecture and ADRs,
API/CLI/MCP references, research/document/workspace guides, migration and recovery
runbooks, changelog, and release notes. Surface inventories and 175 relative links
passed. Incorrect test commands and stale fixture-only descriptions were corrected.
The documentation explains HTTP-only `as_of`, bounded execution, and uncertain
external-call outcomes. Historical experiment records remain historical.

A separate deployed document demonstration used one real model call and no search
calls. Relevant text beyond character 8,000 of a public, versioned document reached
the answer context. The resulting citation resolved to identical source text
through API, CLI, and MCP. Temporary session data was removed. This demonstrates
the document/citation workflow; it is not a comparative search-quality benchmark.

## Vector migration

All stored page vectors and their metadata copied exactly. The original
strict approximate-search comparison failed in 28 of 100 cases and remains failed
evidence. Investigation found tied result orders and three cases where approximate
search missed higher-scoring neighbors.

A separate reviewed exact-search check, using the same frozen 100 stored query
vectors and unchanged 0.0001 tolerance, passed: 76 identical ordered lists and
24 equivalent lists within the score-tie tolerance. Both lists were checked for
unique results, finite scores, score agreement by ID, ordering, and cutoff ties.
This does not change the runtime strict comparator or prove all-query relevance.
The runtime comparison counter was not observed during this offline test; the
runner checked its own comparisons and monitored the declared write counters.

A disposable index write was read back identically from both stores and removed.
Fresh backups were copied off-host and checksum-verified. Qdrant remains available
for rollback.

## Deployment status

Both deployments passed checks against runtime revision
`729d34d360d819b29a633389e0067e34b2dfe7ce` and the verified immutable images.
Mainline now serves page vectors from PostgreSQL, keeps Qdrant dual writes fresh
for rollback, and enables the four live research flags with fixture runs disabled.
Its API and MCP capabilities match the source, including all 74 MCP tools.

Retained research data passed exact-content checks. The installed CLI matches
release source, and API/MCP capabilities match that same revision. Existing client
configuration was preserved. Default Compose settings were activated only after
acceptance and without another container restart. Private operator receipts retain
the detailed deployment checks and any unsuccessful verification attempts.

## Published release

[Version 1.0.1](https://github.com/groktopus/groktocrawl/releases/tag/v1.0.1)
was published on October 16, 2026. The stable release tag points to the exact
verified runtime commit above. The
[image promotion workflow](https://github.com/groktopus/groktocrawl/actions/runs/38910527919)
passed and promoted all ten existing image indexes without rebuilding. Both
amd64 and arm64 image configurations identify the same source revision. Their
index digests match the images used for deployment verification.

The `stable`, `1`, `1.0`, `1.0.1`, and `v1.0.1` image tags were verified. The release
includes [image provenance](https://github.com/groktopus/groktocrawl/releases/download/v1.0.1/release-image-provenance.json),
expanded release notes, upgrade guidance, and the full changelog link. The cleanup
requirements, deployment checks, release tag, and published images are complete.
