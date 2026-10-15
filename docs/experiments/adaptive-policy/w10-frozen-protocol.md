# W10 bounded adaptive-query comparison protocol

Status: **corrected and refrozen before the valid execution**

## Cases and arms

Use all 24 frozen W8 cases as the reproducibility anchor and the 12 cases in
`w10-challenge-cases.json` as the challenge stratum. Report strata separately.
Counterbalance the five policies by case with seed `20260911`:

1. fixed query only;
2. W8-style unconstrained adaptive formulation;
3. one or two queries tied to a declared gap;
4. gap-tied queries that also pass the proposal gate;
5. proposal-gated queries plus marginal-value admission and deterministic stop.

The full policy executes its first admitted follow-up, acquires the bounded
results, and performs an interim evidence assessment. It stops before a second
follow-up when every gap is closed or that first follow-up produced no declared
evidence gain. Otherwise it may execute the second admitted follow-up. All arms,
including the full policy, receive a separate final blind assessment after
acquisition and backfill; the interim assessment is a policy input and never the
final score.

For each case, derive one seeded policy order and rotate it by one position per
repetition. Shuffle case order separately within each repetition. The resulting
work-order file is retained and hashed in the run manifest. A globally shuffled
ten-trial pilot was stopped and excluded after the data-science design audit
showed that it randomized order without counterbalancing policy position.

The search provider, result cap, acquisition path, model family, prompts, and
case data are frozen. Technical execution is repeated three times. Model-created
query text may vary and every variant is retained.

The acquisition budget is eight pages. Fixed retrieval acquires its top eight.
Adaptive arms acquire the top four initial results and the top two novel results
from each executed follow-up; unused follow-up slots are backfilled in original
result order. All returned result metadata remains in the exclusion ledger. This
allocation was clarified after a pre-execution smoke test showed that grading up
to 24 full pages could not finish inside the already-frozen 90-second case bound;
the failed smoke record is retained and is not part of the comparison.

An initial 13-trial execution was stopped and excluded after a methodology audit
found that the implementation assessed evidence only after both follow-ups. It
therefore recorded a stop reason but could not stop between rounds. The excluded
tree is retained as `challenge-excluded-posthoc-stop` with SHA-256
`65bbf54573df711b9de6be7a728340c8412fcb2d0aa0a327c7b0c6afd1dc1949`.
The corrected implementation permits a third model call only for the full arm's
interim assessment and counts that call as policy cost.

A subsequent six-trial launch proved the sequential behavior but exposed an
accounting defect: a completed trial with no proposed follow-up retained the
nonterminal stop label `continue`. That launch is preserved and excluded as
`challenge-excluded-nonterminal-stop-reason` with SHA-256
`c7a1d2dff70fdbd54e1f8fa1b43d5cbdbf360819d50e27771d6ea8099b685db4`.
Every valid completed trial must instead record a terminal reason, including
fixed-query completion, planner-declared completion, no proposed follow-up, no
admitted proposal, exhausted proposals, an evidence-based stop, or a hard bound.
The targeted repeat completed in 50.639 seconds with the explicit terminal reason
`planner_claimed_complete`; its private tree is retained as
`pre-execution-terminal-stop-smoke` with SHA-256
`c92710ce3979dcb1d14948d2ae1d961e3fa244ccddacc6e1db31279452ca766c`.

An eight-trial launch was then stopped when the provenance audit found that a
page seen below the initial acquisition cutoff and later promoted by a follow-up
was still attributed to its earliest sighting. That could hide a real follow-up
gain and stop too early. The launch is retained and excluded as
`challenge-excluded-acquisition-attribution` with SHA-256
`e1840d56394971c2262a69f98fd892df70a7878a968f467700f8a7b4ebaa9d97`.
Valid records distinguish every search sighting from `selected_on_attempt`, the
specific search that caused acquisition; backfill is recorded separately with a
null selection attempt and never credited as adaptive-query gain.
The targeted live repeat completed in 100.269 seconds with eight selected pages:
four from the initial search, two from the first follow-up, and two explicit
backfills. The validator closed every provenance and evidence edge. Its private
tree is retained as `pre-execution-acquisition-attribution-smoke` with SHA-256
`251f7e3b9cd30a900ef87dcddbab84aa0ea0c3d96fda3b4856298ffa438c0670`.

A later nine-trial launch was stopped when the research-methodology audit found
that proposal gating received the original all-open gap definitions rather than
the planner's recorded initial assessment. A gated arm could therefore execute a
query for a gap the planner had already declared closed. The launch is retained
and excluded as `challenge-excluded-stale-gap-gate` with SHA-256
`db6d737cff8c812d28ff2a1da32fe1887eb4e2ac9c44e609c0bb4c22968178ee`.
Valid adaptive records preserve one unique initial assessment for every declared
gap, and proposal admission uses that exact state. The run validator verifies
this closure before accepting a study run.
The two-arm live reproduction then completed without failures and passed the
validator. Both arms preserved all three initial judgments and admitted only
proposals tied to gaps recorded as open. Its private evidence tree is retained
as `pre-execution-gap-state-smoke` with SHA-256
`d243838850f042fdd40cb14e16a9c5af44096b21f1a937eacabff17c836f82a7`.

A four-trial launch was stopped and excluded when the final verification audit
found that the validator checked the completeness of planner gap judgments but
did not independently reject an admitted proposal aimed at a planner-closed
gap. The runner already enforced the rule, but trusting the component under test
would leave the evidence claim unproved. The preserved tree is
`challenge-excluded-validator-closure-gap` with SHA-256
`4d8a867ed1829e2acf827cbce2a00d538c033e5c1be47e999209856bdad6dc45`.
The validator now closes that edge directly.

A subsequent launch was stopped after 12 completed trials when an interim blind
judge declared every gap closed but the separate final blind judge credited only
2 of 7 weighted points. The disagreement is legitimate evidence about stop
reliability, but the record retained only the aggregate round decision and could
not reconstruct the interim per-gap judgments that caused it. The run and its
interruption record are preserved and excluded as
`challenge-excluded-unpreserved-interim-assessment` with SHA-256
`269bdcdd8eb502bae560b153b5320ef6a37503b8176b06423a1d198e035ebc36`.
Valid sequential-policy records now preserve the complete interim assessment,
and the validator checks it against the recorded round decision.
The focused live repeat preserved all three interim gap judgments and the
separate final statuses, completed without failure, and passed the strengthened
validator. Its private evidence tree is retained as
`pre-execution-interim-preservation-smoke` with SHA-256
`2c60978a16e4f0b5b68e125bd77d88f35f405abbe4bb7236b6765f35cffbddcb`.

A later full launch was stopped after 27 attempted trials. Two trials reached a
final assessment whose candidate set was incomplete. The runner correctly
failed those trials, but its archived checkpoint retained the candidates and
the cardinality error without retaining the exact assessment returned by the
model. That prevents reconstruction of which candidate grades were omitted.
The complete launch is preserved and excluded as
`challenge-excluded-unpreserved-final-assessment`; its canonical 63-file
manifest (relative path, byte size, and content SHA-256, encoded as compact
sorted-key JSON) has SHA-256
`430836f5fe67606d05de0bf439bce720de58ce2b57c3fbeecac0f4056d7f85ce`.
The checkpoint is now written with the received assessment and its model-call
receipt before candidate or gap cardinality is validated. The independent run
validator rejects an assessment-stage failure checkpoint that lacks either.
The corrected runner then completed a one-case fixed-policy smoke against the
pinned candidate and local model route, and the independent validator reported
no issues. Its five-file evidence tree is retained as
`pre-execution-final-assessment-preservation-smoke`; the canonical manifest has
SHA-256
`ab71da5cacc847021bbfcc94e80fbc8f813eabaf303a2176905f0c1203171e65`.

The first attempted relaunch was stopped during its first trial because the
operator-supplied full source revision did not match the checked-out commit.
It is retained and excluded as `challenge-excluded-launch-commit-typo`; its
canonical six-file manifest has SHA-256
`89ed1c5c8c37133395189106417cdfc011979b460c5ed66e8793a86d9ca7145d`.
No result from that launch is eligible for analysis.

The next clean launch was stopped after 24 terminal attempts (21 completed and
three failed) with one additional trial in flight. Each failed final assessment
returned eight candidate grades but reused one or more candidate IDs, omitting
the same number of candidates. Retrying until an array happened to contain
unique identities would condition inclusion on model-formatting luck. The
57-file evidence tree is retained and excluded as
`challenge-excluded-array-assessment-identity`; its canonical manifest has
SHA-256
`d3e96107945173839d3489c846f5f93cfacf2744da105c9d556218c5d6c1ebb6`.

Candidate and gap assessments now use their frozen IDs as required object keys
with additional properties forbidden. The runner checkpoints that exact wire
response, verifies its key sets, and only then normalizes it to the unchanged
analysis record shape. This enforces one grade per identity in the original
model call without repair calls, invented mappings, or valid-output selection.
The keyed schema then completed a one-case gap-policy smoke using the same
freshness case and policy combination that had failed in both prior full
launches. The independent validator reported no issues. Its five-file evidence
tree is retained as `pre-execution-keyed-assessment-smoke`; the canonical
manifest has SHA-256
`2258d1d76015393a14482d27ee1a7055f8a716f04f21db1a3a7c63b0f19f4366`.

The first corrected one-case smoke reached that interim decision but the final
blind assessment exhausted the former 90-second ceiling. It is retained outside
the comparison as `pre-execution-sequential-stop-smoke` with SHA-256
`c748c01a83235951369a47375cec0c95f516a528e7376fb473493ceada0b6bc3`.
Because the additional assessment is required for the policy to observe its own
stopping condition, the valid execution uses a 180-second ceiling and retains
actual elapsed time and model-call count for every arm.

The repeated one-case smoke then completed in 72.153 seconds with two searches
and three model calls. The interim assessment found no material gain, the policy
stopped before dispatching the second proposed query, and the final blind
assessment completed successfully. Its private evidence tree is retained as
`pre-execution-sequential-stop-smoke-180s` with SHA-256
`6b49cec895009684b37c268004bd5967d60c48288520b7ee7f6c293bc147ce66`.

## Bounds and gates

Each case permits at most three searches, three planning/judging model calls, 20
results per search, eight admitted sources, and 180 elapsed seconds. A proposal
must name one gap, predict the evidence that closes it, differ materially from
prior query intent, and avoid broadening beyond the material claims.
The named gap must still be open in the planner's preserved initial assessment.

A candidate is admitted only if acquired content supports or challenges a
material claim, improves authority or currency, resolves a contradiction, or
adds an independent publisher. Snippets and inaccessible pages remain logged but
cannot close a claim. Canonical duplicates and derivative reporting do not add
marginal value.

Stop when every material gap meets its case-specific closure rule; after a round
with no material claim, authority, currency, or contradiction gain; or at a hard
bound. Publisher independence remains a source-admission gain, but cannot alone
justify another query without evidence relevant to a declared gap. The stop
reason is computed from recorded state before a second follow-up is dispatched.
LangGraph carries the gap, proposal, decision, candidate
disposition, marginal value, and stop state; the deterministic policy owns the
decision.

Policy 5 replaces fixed retrieval for a named challenge type only if, in that
stratum and in at least two of three repetitions, it:

- improves weighted claim closure by at least 10 percentage points;
- leaves no more unsupported high-importance claims;
- keeps admitted-source precision within 5 percentage points of fixed retrieval;
- has at most 10% unnecessary executed queries after the last evidence gain;
- stays within every per-case work bound and has no higher failure rate.

Across the W8 anchor, it must not reduce weighted closure or precision by more
than two percentage points. If no policy clears all gates, fixed retrieval stays
the default and the result may still identify a narrower recovery policy.

## Evaluation and blindness

The primary unit is a declared claim gap. Weighted closure equals closed claim
weight divided by total claim weight. A source is useful when acquired content
materially supports or challenges at least one declared claim. Precision is
useful admitted sources divided by all admitted sources.

Grade relevance, source quality, and claim closure separately. Source quality
scores currency, relevance, authority, accuracy, and purpose from 0 to 2 each;
it does not substitute for claim support. Preserve contradictions. Record copied
or derivative relationships when visible.

Candidate records are randomized and arm/rank labels sealed before grading. A
frozen model grader scores acquired excerpts. Manually adjudicate all grader
schema failures, all high-importance closure disagreements, and a seeded 10%
sample. Report cluster-aware case distributions rather than treating source
sightings as independent observations.

The runner writes a public query/candidate checkpoint and a separate private
acquisition checkpoint before grading. A grader or transport failure archives
both checkpoints, including excluded candidates, before the trial is retried.
Failure evidence is never treated as a policy outcome, but remains available for
the required manual adjudication and missing-data audit.

After a model assessment returns, the public checkpoint also retains the exact
structured assessment and response digest before semantic coverage checks run.
Invalid candidate or gap cardinality is therefore reproducible rather than
reduced to an error string.

For the full policy, the public evidence record preserves the complete interim
candidate and gap assessment that caused a between-round stop or continuation.
The final blind assessment remains separate. Any `all gaps closed` interim stop
that disagrees with final closure is retained as a stop-reliability diagnostic
and included in adjudication; it does not alter the frozen replacement gates.

## Sensitivity and missing data

Repeat the decision after removing unavailable sources, removing each challenge
case in turn, using equal claim weights, and counting ambiguous closure as open.
Exclude a case only when no arm can access evidence needed to score any declared
claim; preserve the case and unavailable candidates otherwise.

## Evidence package

Retain the environment manifest, query/proposal log, candidate and exclusion log,
claim-gap ledger, source-to-claim links, blind grades, sealed arm map, run-level
measurements, hypothesis analysis, sensitivities, and outcome. No essential
evidence may exist only in chat or an untracked temporary file.

At launch, write immutable run metadata containing the exact 40-character source
commit and digests for the case file, freeze record, and runner. This record must
exist before the first network request so an interrupted run retains its identity.

The source and exclusion accounting, competing-hypothesis analysis, and durable
completion gate are defined in `w10-research-log.md`. Live execution uses the
same optional dependency as the earlier runtime studies,
`langgraph==0.6.11`, installed in an isolated experiment environment rather than
the production image.

W10 uses the separately deployed, pinned flat-search baseline recorded in
`w10-search-environment.json`: GroktoCrawl candidate image
`6b46bc9a3f44a1571ad68d5453fef90edc99466b` and SlopSearX 0.4.0 source
`f4c7f960f09a1b2ce931d1e0bd2bf1a171cb5222`. The runner binds that record's
digest into immutable run metadata and the final manifest. SlopSearX 0.5's
opt-in MCP workflows are outside this comparison and belong to W11.

One trial launched immediately after notification of the incumbent host upgrade was
stopped conservatively. Subsequent route inspection proved W10 was using its own
unchanged SlopSearX 0.4.0 container rather than incumbent host. The one completed trial
and interruption evidence remain excluded as
`challenge-excluded-search-environment-change` with SHA-256
`86b301d24734f5f6dfe79e2e3b807907c7824af2921b9eb0855fae0dee6b4301`.
The environment-binding live smoke then verified both deployed image IDs before
execution, completed without failure, embedded the SlopSearX package version and
source revision in its manifest, and passed the validator. Its private evidence
tree is retained as `pre-execution-environment-binding-smoke` with SHA-256
`7bf70770343d0b36f4add2e0115ed007f803d02c0b55590eae2a75c08e969b9a`.
