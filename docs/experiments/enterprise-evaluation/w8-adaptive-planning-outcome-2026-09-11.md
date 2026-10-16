# W8 bounded query-planning outcome — 2026-09-11

Status: **query formulation shows value; this stopping policy is rejected**

The clean comparison replayed the frozen first search for all 24 W8 questions,
then allowed the local model one planning call and at most two distinct follow-up
searches. The model saw titles, URLs and snippets, but never the known-useful URL
judgments used for scoring. Application code owned the search, result, model-call,
output-token and 90-second time limits.

| Measure | Single pass | Bounded planner |
|---|---:|---:|
| Completed questions | 24/24 | 24/24 |
| Exact known-useful URLs | 17/44 (38.6%) | 20/44 (45.5%) |
| Questions with a known-useful URL | 14/24 | 16/24 |
| Searches | 24 | 69 |
| Results inspected | 463 | 1,241 |
| Model calls | 0 | 24 |
| p50 / p95 end-to-end latency | 551 / 1,103 ms | 7,908 / 10,709 ms |
| Planner prompt / completion tokens | 0 / 0 | 57,467 / 4,343 |

The added queries found three more frozen URLs and raised two previously empty
questions to at least one known source. Long-tail and terminology-shift slices
improved; ambiguous-entity and recent slices did not.

That gain cost 45 searches, 24 model calls, 778 additional inspected results and
roughly fourteen times the median latency. More seriously, the model called every
first pass `weak`. It never concluded that the initial evidence was sufficient and
produced only one opposition query in the clean run. It therefore demonstrated useful
query formulation, but not trustworthy sufficiency judgment, selective spending,
or contradiction discovery.

## Failure injections

The executable contract rejects repeated queries, excessive searches, unknown
purpose labels, incoherent stop decisions and overlong fields. Tests preserve a
contradictory signal and opposition search, deduplicate repeated result URLs,
retain an unavailable-source failure and unresolved need, and stop before dispatch
when time is exhausted. Ten focused tests pass. The controller has no unbounded
edge: one model call admits zero to two follow-ups, and application code dispatches
each at most once.

## Runtime decision

Keep the imperative controller as the reference. This live result does not justify
putting LangGraph on the default path: the weak point was evidence judgment, while
the executed control flow was one decision followed by at most two searches.
LangGraph would express that same small flow with another state machine.

Retain LangGraph as the optional advanced runtime selected by
[ADR-0073](../../adr/experimental/groktocrawl-x/0073-compare-research-runtimes-under-one-policy.md). The prior
[future-runtime evidence](../evidence/future-runtime/2026-09-09/README.md) already
shows its conditional edges and dynamic fan-out can express bounded replanning
under the shared ledger. Its stronger future case remains durable interrupts,
checkpoint forks, evolving long-running graphs and specialist subgraphs. A future
iterative planner should be compared through both runtimes only after its evidence
state can discriminate adequate from weak results.

## Decision

Do not add this planner to every search. Carry query formulation forward as an
optional recovery tool, triggered by deterministic missing-evidence signals and
charged to the application budget. Replace the model's all-weak stopping decision
before any production pilot. W8 now moves to source diversity and contradiction
measurement; those experiments can establish the evidence signals that selective
replanning needs.

## Private evidence

The clean packet is frozen at
`sha256:6ccb1cc1148927a1607831eec2cdee6367a54a89948f89194c327cfe556defaf`.
The raw result ledger remains private because it contains held-out questions and
model-generated queries. Four earlier attempts are retained separately: one
invalid hostname run, two output-contract hardening runs, and one clean but
non-randomized run. No production configuration or adoption decision changes.
