# Research experiment acceptance review

W1 review packet for [issue #13](https://github.com/magnus919/groktocrawl-x/issues/13),
under [experiment tracker #1](https://github.com/magnus919/groktocrawl-x/issues/1).
**Decision recorded 2026-09-05:** Magnus accepted ADRs 0067–0070 and 0072 for the
bounded fixture-backed prototype described below. ADRs 0071, 0073 and 0074 remain
proposed. Comparative execution and remaining implementation gates are not waived.
See [acceptance issue #15](https://github.com/magnus919/groktocrawl-x/issues/15).

## Concrete next decision

Magnus accepted the **foundation contracts** in ADRs 0067–0070
and 0072 for a bounded, fixture-backed W2 prototype: experimental identity;
separate execution/knowledge/rendering; versioned evidence and verification;
separate policy/runtime evaluation; and a distinct experimental client contract
with no unaudited answer text. The explicit maintainer decision is recorded in ADR/index metadata and scoped
successor links; merging the original review packet did not itself accept them.

Acceptance establishes design direction, not claim that semantic verification
works, authorize paid runs, or complete W1. Finish the applicable baseline/preflight
gates before comparative execution and the implementation prerequisites in the
plan before W2. Prototype issues must state their boundaries and evidence gates.

| Decision | Reviewable recommendation | Defer / evidence still required |
|---|---|---|
| [0067 charter](../adr/experimental/groktocrawl-x/0067-establish-an-independent-research-architecture-experiment.md) | Independent experimental fork with explicit adoption/stop gates | Mainline replacement or publication is outside scope |
| [0068 boundaries](../adr/experimental/groktocrawl-x/0068-separate-research-execution-knowledge-and-rendering.md) | Operational state separate from Knowledge IR and audited renderings | Runtime/service topology selection |
| [0069 knowledge](../adr/experimental/groktocrawl-x/0069-define-versioned-knowledge-and-verification.md) | Immutable evidence, scoped claims, conflict preservation and render audit | Executable schemas, negative fixtures and independent verifier assessment |
| [0070 evaluation](../adr/experimental/groktocrawl-x/0070-evaluate-research-policy-and-runtime-separately.md) | A/B policy and B/C runtime comparisons with frozen manifests | Named reviewers, corpus, budgets, measured thresholds and paid-run authorization |
| [0071 storage](../adr/experimental/groktocrawl-x/0071-store-research-evidence-independently-of-sessions.md) | Review PostgreSQL bounded-byte authority as a separate storage choice | Real-database lifecycle/restore gates and pgvector/Qdrant comparison; no service selected by this packet |
| [0072 clients](../adr/experimental/groktocrawl-x/0072-expose-verified-research-through-an-experimental-protocol.md) | Experimental route family; progress then one audited result | Full schemas/limits and HTTP/CLI/MCP traces before shipping |
| [0073 runtime](../adr/experimental/groktocrawl-x/0073-compare-research-runtimes-under-one-policy.md) | Imperative reference versus one LangGraph comparison candidate; optional adapter merged in PR #150 | Candidate is not adopted runtime; exact dependency pin, W1 baseline inputs, W4 evidence and decision remain required |
| [0074 recovery](../adr/experimental/groktocrawl-x/0074-define-research-recovery-before-selecting-infrastructure.md) | Review ownership/fencing/ambiguity target separately from technology | Timing/retention limits before W5; crash matrix and infrastructure decision before adoption |

Storage acceptance can proceed separately when its recommendation is reviewed.
If PostgreSQL is adopted, preserve the pgvector replacement evaluation rather than
making Qdrant a permanent additional database by default. Recovery's conditional
PostgreSQL-native option is also a comparison candidate, not a selection.

## Evidence available now

[The draft preflight manifest](research-preflight.json) pins exact file SHA-256s,
commit identities, request hashes and observed grader outcomes. It is a readable
planning artifact; no application or CI gate consumes this new format yet. Its
`comparison_authorized: false` and unresolved values prevent a reader from mistaking
it for an approved complete series. Implementation of a preflight validator must
fail closed on missing applicable fields before a comparison runner uses it.

On current-fork commit `280761304f4d0eb58ee1680d7f7adced559ef0a1`, the inherited broad
harness matched its pinned baseline: 12 cases, 11 passing outcomes and the intended
mis-citation failure control. Four cases are negative/abstention (33.3%). Run
`local-1-1788578735` used source fixture v3 and LLM fixture v2, in-process with a
harness-local scrape stub. No Docker or external provider was used by this run.
Pinned baselines were not changed. The manifest retains sanitized outcomes and
input pins; the full local report at `/private/tmp/groktocrawl-x-incumbent-baseline.json`
is supplementary and not required to locate the corpus or reproduce the run.

This establishes the current regression outcome, not general factual quality or
Knowledge IR conformance. Some graders have no applicable declared claims/citations
in a particular fixture; a pass is not evidence for unexercised semantics. The
synthetic Acme Archive example supplies a proposed conflict/IR contract, not an
executed verifier benchmark or a held-out corpus.

## Reproduce the incumbent regression observation

Use a clean checkout at the pinned current-fork commit, sync the locked fast-test
environment as described by repository CI, and run from its root:

```bash
export PYTHONPATH=agent-svc:scraper-svc:llm-svc:slopsearx-fixture:parse-svc:portal-svc:browser-svc:semantic-svc:.
.venv/bin/python scripts/run_answer_evals.py --selection broad --dry-run
.venv/bin/python scripts/run_answer_evals.py --selection broad --json
```

The harness writes generated results under ignored `eval-out/`. Compare against the
existing pinned baseline; do not pass `--record-baseline` or promote new outcomes as
part of reproduction. Verify the manifest's file hashes first. Platform timings can
differ and are not a latency baseline. Later fixture/harness changes require a new
versioned evidence record rather than editing the historical observation.

Arm A remains the original upstream commit from the plan; it has **not** been run
as an A/B/C comparative arm. The current fork includes fixture/CI corrections and
must not silently replace A. Before comparison, define and review any harness-only
compatibility adapter required to run A against the frozen comparison corpus; pin
that adapter and show it preserves incumbent policy. B and C do not exist yet.

## Work that still blocks W1 comparisons

- Curate development and held-out question sets with expected subquestions, supported
  claims, qualifiers and independent source context. Pin hashes and separation.
- Assign the primary reviewer and adjudication role. If only one human is available,
  disclose the limitation under ADR-0070; another model is not a second human.
- Measure the selected baseline workloads, then freeze absolute/relative latency,
  resource, quality and coverage bounds with rationale before candidate outcomes.
- Freeze per-operation/run/series ceilings, hardware, ordering, versions and the
  uncertainty plan. Default external-provider spend remains zero.
- Resolve recovery timing/retention only for the later W5 contract; do not require
  a fictitious recovery measurement to admit a non-durable fixture prototype.

The manifest's nulls are intentional blockers, not hidden defaults. The proposed
sample minima from ADR-0070 are not a completed power analysis or approved paid
series. Existing twelve-case fixtures are a regression seed, not the required
held-out quality dataset. No runtime speedup, storage reliability or recovery claim
can be made from this packet.

Issue [#116](https://github.com/magnus919/groktocrawl-x/issues/116) adds the
fail-closed validator for this manifest. Run
`python3 scripts/validate-research-preflight.py` before any comparison runner;
the current draft is expected to fail with unresolved baseline, reviewer,
threshold and pin diagnostics.

W0 also remains incomplete: repository ruleset enforcement and publishing identity
are unresolved. Hosted checks passing does not prove GitHub enforces merge policy.
Keep inherited publishing and paid/live workflows disabled. The next implementation
issue must link its accepted decisions and applicable evidence; do not generate
additional ADRs merely to restate these gates.
