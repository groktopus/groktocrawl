# Future deep-research runtime scenarios

This is the pre-measurement specification for issue
[#245](https://github.com/magnus919/groktocrawl-x/issues/245). It asks whether
LangGraph provides a useful foundation for research capabilities GroktoCrawl does
not yet offer. It does not assume an enterprise product or select a runtime.

## Product standard

The desired substrate helps a person get a trustworthy answer to a difficult
question. It should adapt when early evidence is weak, let useful specialists work
together, accept guidance without throwing work away, preserve completed work
through interruption, and take advantage of better models and search tools without
changing the public research contract.

Framework features count only when they improve that experience or materially
reduce the difficulty of building it. A graph diagram, checkpoint row, or successful
demo is not by itself a benefit.

## Shared boundary

Both experiment arms use the same application-owned contracts:

- PostgreSQL owns evidence, authorization history, manifests, and artifact bytes.
- The accepted W5 execution ledger owns admission, budgets, operation identity,
  fencing, cancellation, retries, and terminal projection.
- Runtime checkpoints may contain control state and immutable references. They may
  not become another evidence or artifact authority.
- HTTP, SSE, CLI, and MCP expose `research/1`, never framework node names or state.
- A completed external operation is reused by its stable receipt. Resume, replay,
  and fork may not repeat it silently.
- Deletion and scope fences apply before resume, fork, tool use, and publication.

The imperative arm may add explicit state and dispatch code needed to support a
scenario. The LangGraph arm may use native conditional edges, dynamic `Send`,
subgraphs, interrupts, checkpoints, and state history. Both arms call the same
scripted model, search, browser, evidence, budget, and publication adapters.

## Scenario 1: change the plan when evidence is weak

**User journey.** A person asks a broad question. The first search produces thin
or contradictory evidence. The system explains the gap in its progress stream,
creates a narrower follow-up query, and stops replanning once coverage is adequate
or the budget is exhausted.

**Required behavior.** The same weakness signal yields the same follow-up intent in
both arms. Every added operation reserves budget first. The loop has explicit depth,
operation, time, and cost limits. Replanning cannot erase contrary evidence or
publish unaudited prose.

**Evidence.** Record plan revisions, why each revision occurred, operations and
receipts reused, final coverage, terminal reason, code needed for the new branch,
and the ease of understanding a failed run.

## Scenario 2: create specialists from the question

**User journey.** A question spans several fields. The system creates only the
specialists the question needs, runs them concurrently, then performs a stable
synthesis that preserves disagreements and source lineage.

**Required behavior.** Specialist identity derives from a canonical assignment.
Parallel updates use explicit reducers and cannot overwrite one another. Completion
order cannot change the final evidence set. Cancellation reaches every branch and
late work cannot publish.

**Evidence.** Exercise one, three, and changing specialist counts; reverse branch
completion; add a new specialist type; and compare state plumbing, reducer mistakes,
trace clarity, implementation size, and elapsed scheduling overhead.

## Scenario 3: ask the user, restart, and continue

**User journey.** Research reaches an ambiguity that affects scope or priorities.
The system pauses with a plain-language question. After guidance and a process
restart, it continues from the pause without repeating completed searches or model
calls.

**Required behavior.** The interrupt is durable and tied to the admitted run,
policy, state schema, and authorization scope. Guidance is validated and recorded
as an auditable input. Resume reacquires W5 ownership before dispatch. A deleted,
expired, cancelled, foreign-scope, or incompatible run cannot resume.

**Evidence.** Kill the process before and after the interrupt checkpoint and after
guidance acceptance. Count adapter calls and prove completed receipts are reused.
Inspect both the application ledger and LangGraph checkpoint to show which one owns
each fact.

## Scenario 4: fork an earlier investigation

**User journey.** A person wants to explore a competing assumption from an earlier
point without losing the original investigation. The system creates a new run that
reuses eligible evidence and completed operations, records its ancestry, and
publishes a separate artifact set.

**Required behavior.** Forking never mutates the parent. Reuse requires compatible
policy, model/tool identity, scope, retention, and authorization. Changed inputs get
new logical operations. Deleting either root cannot expose the other through stale
checkpoint references.

**Evidence.** Fork before and after specialist fan-out, change one hypothesis, and
verify receipt reuse, new operation counts, provenance, independent cancellation,
and deletion. Compare native state-history support with explicit imperative cloning.

## Scenario 5: replace a model or search capability

**User journey.** A better model or search adapter becomes available. The platform
can use it for newly admitted work, while existing runs resume under their pinned
semantics and clients continue to receive the same public contract.

**Required behavior.** Admitted runs pin runtime, policy, state schema, model, search
adapter, and receipt semantics. Unsupported old state is quarantined or handled by
a compatible worker. Upgrading cannot reinterpret old receipts or silently restart
the investigation.

**Evidence.** Add one scripted search capability and one specialist stage, resume an
old checkpoint with compatible code, reject an incompatible state version, and
compare migration code, worker routing, public traces, and rollback effort.

## Measurements

Each scenario produces a machine-readable result for both arms:

| Dimension | Recorded evidence |
|---|---|
| User capability | Journey completed, terminal reason, guidance/replan/fork visible in application events |
| Correctness | Invariant violations, duplicated external calls, missing or changed evidence, authorization/deletion failures |
| Extensibility | Files and contract surfaces changed, new state fields/reducers/nodes, adapter reuse, implementation notes reviewed from the final diff |
| State evolution | Schema/version declarations, compatible resume, incompatible-state behavior, migration and rollback steps |
| Observability | Can an operator identify current plan, branch, receipt, pause, owner, and publication state from retained traces? |
| Performance | End-to-end fixture time, framework-only scheduling time, checkpoint count and bytes, warm and cold repetitions |
| Operations | Added packages, tables/services, cleanup needs, recovery steps, and failure diagnosis |

Code size is descriptive, not a productivity score. Fixture latency does not predict
model or network latency. Deterministic callbacks test control behavior rather than
research quality.

## Decision rule

Hard invariants permit zero failures. Retain the imperative controller as the
reference if LangGraph merely expresses the same capability with another state
model. Retain LangGraph as an optional advanced runtime if it makes at least one
valuable scenario substantially clearer or easier to extend while preserving the
authority boundary. Adopt it as the preferred substrate only if benefits repeat
across scenarios and its persistence, migration, and operating costs are acceptable.

Reject or pause the candidate if native checkpoint behavior conflicts with W5
ownership, external effects repeat, state forks weaken authorization or deletion,
or framework state leaks into the client contract. Reconsider any decision when
model/tool capabilities, LangGraph persistence semantics, or the research product's
needs materially change.
