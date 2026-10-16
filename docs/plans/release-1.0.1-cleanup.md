# Complete the 1.0.1 integration cleanup

Status: all ten cleanup requirements verified; release publication is being finalized.

The 1.0.0 integration left the new durable research endpoint connected to example data. The correction must complete the intended user workflows and check the other release claims against actual behavior. The table below defines the completed cleanup scope; supporting evidence follows it.

## Work and acceptance

| Work | Required result | Evidence needed before completion |
| --- | --- | --- |
| Live durable research | A submitted objective drives configured search, capture, model review, and retained reports | Two different objectives through HTTP and MCP produce different acquired evidence and reports; fresh-process reads preserve exact bytes |
| Research breadth | Research can plan and execute multiple queries and acquire more than three sources within explicit budgets | A controlled multi-query investigation needs and uses evidence beyond the third source; every external operation has a stable receipt |
| Completion callbacks and time settings | Supported request settings affect execution; completion/failure callbacks survive interruption | Request-to-behavior tests, safe destination validation, persisted delivery and duplicate-handling tests; documented time semantics |
| Workspace follow-up | An explicit follow-up runs a new investigation using the selected evidence | Preview-to-execution tests through API, CLI and MCP, scoped source selection and independent result identity |
| Research attachments | Attached research evidence can be selected when asking a session question | Attach, ask, resolve citation, and verify exact retained source bytes; ownership, expiry and deletion tests |
| PostgreSQL-only operation | Research memory and page vectors can operate without Qdrant after migration | Memory retrieval/write/delete/sweep parity, migration verification, startup and indexing without Qdrant; legacy and rollback modes retained |
| Existing background jobs | Interrupted work has durable ownership, progress, recovery and truthful treatment of uncertain external calls | Restart and competing-worker tests for crawl, agent, extract, batch scrape, llmstxt and plan execution; cancellation, artifact consistency and webhook delivery tests |
| Document workflow | Upload, parsing, session question and exact citation retrieval work together | Integrated test with real Parse and Valkey plus controlled model responses, including API/CLI/MCP client paths |
| Evidence and capture | Passage selection and automatic recovery work across service boundaries | Relevant late-source passages reach answering context; controlled challenge service exercises real recovery transport and caller-visible outcomes |
| Documentation | Current instructions describe the final implementation accurately | Audit README, AGENTS.md, CONTRIBUTING.md, architecture/ADR index, guides, API/CLI/MCP reference, Compose examples, runbooks, changelog and release notes against final source and tests |

## Completion evidence

Implementation PRs [#670](https://github.com/groktopus/groktocrawl/pull/670),
[#671](https://github.com/groktopus/groktocrawl/pull/671),
[#672](https://github.com/groktopus/groktocrawl/pull/672),
[#673](https://github.com/groktopus/groktocrawl/pull/673), and
[#674](https://github.com/groktopus/groktocrawl/pull/674) passed required checks and
merged. Documentation corrections merged in
[#675](https://github.com/groktopus/groktocrawl/pull/675). The separately reviewed
migration comparison tool merged in
[#676](https://github.com/groktopus/groktocrawl/pull/676).

The [verification report](../experiments/release-1.0.1-verification.md) maps all ten
requirements above to their implementation and acceptance evidence. It records
exact-release CI, deployment checks, retained data, document/client behavior,
vector migration, and limitations. Controlled integration tests establish behavior;
they are not evidence of a measured improvement in live-model answer quality.

## Documentation audit

The audit checked README, AGENTS.md, CONTRIBUTING.md, architecture and ADRs,
API/CLI/MCP references, research/document/workspace guides, Compose examples,
migration and recovery runbooks, changelog, and release notes against source and
tests. Surface inventories passed, and all 175 relative links in the audited
Markdown resolved.

Corrections include live versus fixture research, legacy queued-job recovery and
unknown outcomes, durable callbacks, storage prerequisites, explicit follow-ups,
HTTP-only evidence-date settings, and commands that run tests from the checkout.
ADRs 0068, 0070, 0071, and 0072 are accepted; superseded decisions and experimental
results remain historical records. The migration runbook now distinguishes the
failed strict approximate-search comparison from the passing exact-rank check.

## Release verification

The exact release implementation is
`729d34d360d819b29a633389e0067e34b2dfe7ce`. Both final integration suites passed
2,798 tests, with 162 skips and 17 deselections recorded separately. All ten
multi-architecture images passed build and provenance checks. Both deployments
passed their applicable acceptance checks; mainline now serves PostgreSQL vectors
and live durable research. Existing data and client configuration were preserved.

The release verification report records final publication and image-tag checks.
The cleanup changes are confined to GroktoCrawl.
