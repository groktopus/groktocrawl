# Complete the 1.0.1 integration cleanup

Status: in progress. This plan is a release checklist, not a claim that the changes are available.

The 1.0.0 integration left the new durable research endpoint connected to example data. The correction must complete the intended user workflows and check the other release claims against actual behavior. Release 1.0.1 follows completion of every item below.

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

## Delivery sequence

Use focused pull requests for durable execution, storage, legacy job recovery, workspace integration, and remaining verification/documentation. Each pull request must pass its required checks on the current head and address one review pass before merging. Keep progress and evidence links here as changes merge. Do not release from a partial checklist.

Architectural changes require new decisions where existing ADRs no longer apply. Preserve historical experiment results and superseded decisions as history; do not rewrite them to imply capabilities were already implemented. Reserve ADR numbers centrally to avoid concurrent collisions.

Do not change SlopSearch. Use isolated test services and controlled search/model/capture responses for repeatable acceptance. Preserve existing deployment data and client configuration. Record actual test limitations rather than treating collection, fixture counts, or successful health responses as proof of user outcomes.

## Verification in progress

PR #671 includes `tests/integration/test_document_session_journey.py`: a PDF is
uploaded through the session API, parsed by the real Parse HTTP service, stored
in isolated Valkey, used in a session question, and read back as an exact citation.
The test also checks foreign-owner rejection and evidence removal after deletion.
The same journey runs the CLI as a subprocess and dispatches MCP upload, query,
and read tools against the local HTTP API. A second document places the answer
beyond character 8,000 and verifies that passage reaches the model and can be
read back exactly through MCP. The model response is controlled;
this verifies service and client integration, not model answer quality. Final
deployment checks remain required.

## Documentation audit

Keep this audit open until the final code is merged. A corrected paragraph is not
proof that the described workflow passes.

| Documents | Compare against | Completion requirement |
| --- | --- | --- |
| README and quick start | Compose defaults, feature flags, service health and public capabilities | A new operator can distinguish live features from test fixtures and start the documented configuration |
| AGENTS.md and CONTRIBUTING.md | Current source layout, test workflows, job ownership and error handling | Commands and development guidance match the implementation and required CI |
| Architecture guide and ADR index | Durable research, legacy job recovery, storage backends and workspace integration | New decisions are indexed; obsolete contracts are clearly superseded without rewriting historical evidence |
| Research, document and workspace guides | API routes, CLI parsing, MCP tools, ownership and retained evidence | Examples exercise the actual operation and explain expiry, deletion and uncertain outcomes |
| Deployment and recovery runbooks | Compose profiles, schema migrations, backup/restore and callback delivery | Prerequisites, rollback steps and recovery limits match the tested implementation |
| Public surface inventory and references | Routes, command flags, tool signatures and environment settings | Automated inventory checks pass and manual behavior review finds no unsupported promises |
| Changelog, release notes and announcement | Merged changes and final acceptance results | Describe verified user benefits and material limitations; no unsupported quality claims |

Workspace and follow-up corrections are included in PR #671. Storage and restart
instructions must be reconciled again after their implementation PRs merge.

## Release completion

After all changes merge, run the complete required checks on the exact release commit, verify the built images, and perform the documented deployment and user-workflow checks. Confirm that the release notes and announcement match those results. Publish 1.0.1 only when every row above has linked completion evidence.
