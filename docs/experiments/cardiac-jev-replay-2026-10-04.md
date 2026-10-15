# Frozen cardiac corpus: Jev question and request-shape study

## Registered protocol

Registered before new Jev calls on 2026-10-04 UTC. This is experimental development
and replay evidence, not fresh held-out validation or medical advice. The source
is the [upstream EXP-036 cardiac packet](https://github.com/magnus919/SlopSearX/blob/bc03032322d3f801454eb2f8119f705e9e1fd2cb/docs/experiments/evidence/EXP-036/q10-frozen.json).
The copied 44-card corpus has SHA-256
`1d472ef33f4a251711090aee3744bc4f75d3c44b90e88a13ab3797a72947f10c`.
No new search, Brave request, page fetch, deployment, or default change is authorized
by this protocol. The owner authorized these public cards to TypeSafe and permits
bounded Jev calls. Secrets and private transport details stay outside publication.

Three GPT-6 Luna agents reviewed question wording, corpus/labels, and comparison
design. Root checks their findings and owns the serial provider-call ledger.

### Questions and hypotheses

W0 preserves the previous ten-level Score wording and criteria. W1 independently
asks a four-level Score about substantive evidence visible in the card and a
three-option Choice about whether visible signals justify opening the source.
A promising bibliographic lead may deserve fetching while containing little
usable evidence. Neither judgment establishes unseen content, medical truth,
source novelty, or actual downstream benefit. The full trusted wording and
criteria are frozen in [contracts.json](evidence/cardiac-jev-replay-2026-10-04/contracts.json).

W0 versus W1 compares different model-plus-question contracts, not an isolated
wording ablation: scale, question count, and judgment dimensions also change.
Primary comparisons are within-contract order/cutoff/repeat stability. Quality
comparisons across contracts are descriptive rankings, never calibrated scores.

Hypotheses: separating retrieval priority from visible evidence reduces promotion
of bibliographic-only cards in an evidence-first ranking; scoring all 44 exposes
useful tail candidates when present; scores remain reasonably stable when
presentation order changes; singleton context may change judgments even with
identical per-card instructions and content. Each may be rejected or unresolved.

### Fixed matrix and budget

- 12 shared-state requests: first 40 versus all 44 × original/reverse/seeded
  shuffle × W0/W1. First-40 membership always means original `c0`–`c39`, even
  after reordering. Seed `91944`; first-40 shuffle projects the same all-44 shuffle.
- Four exact repeats: all-44 original and shuffle, once for each contract.
- Twelve singleton W1 requests: `c0,c6,c11,c13,c20,c21,c22,c25,c27,c29,c35,c43`.
  They preserve per-card instructions and content while changing surrounding
  state and sibling questions; this diagnoses a combined context/batching change.
- First pilot: all-44 original W0, all-44 original W1, singleton `c43`. Stop
  expansion if schema/model/membership validation fails. Pilot calls count
  toward the fixed 28-request ceiling; no automatic retries or replacement calls.
- One request in flight, 128,000 request bytes, 2,000,000 response bytes, 45-second
  transport deadline, 2,000,000 reported input tokens, 40-minute execution bound.
  A completed call can cross the token ceiling; that stops every subsequent call.
  Missing usage or unknown model identity prevents expansion. No production
  latency or queue-capacity claim from this private diagnostic transport.

Exact requests, membership and body hashes are in
[requests.json](evidence/cardiac-jev-replay-2026-10-04/requests.json).
The model is pinned to `jev-1.13.0`; requested and returned identities are recorded.
The [current provider API](https://docs.typesafe.ai/api) was checked before calls:
it supports typed Score/Choice and structured state. Failed `.md` documentation
fetches were supplemented by the live HTML API and primitive pages.

### Analysis frozen before calls

Reuse saved historical first-40/shared-state and whole-set/card-local receipts
as historical context, not fresh matched arms. New requests all use shared state
except explicitly identified singletons.

Sort primary rankings by descending evidence score, with numeric candidate ID
as the fixed tie break independent of presentation order. For W1 also report
lead-first ranking by `P(fetch)`, then evidence score, then numeric ID. No fitted
weights or action thresholds. Append the unscored tail in original order for
first-40 arms. No candidates are silently excluded or discarded.

Report nDCG@10 and useful@10 under both preserved assistant reference sets,
facet coverage, original-rank-41–44 exposure, empty-snippet promotion, and source
concentration. Labels are exploratory assistant judgments, not human gold.
They assess visible cards and cannot validate fetch-worth judgments. Report
rank/top-ten overlap and normalized-score shifts across input orders and exact
repeats, and paired singleton/shared-state shifts. Keep every failure in the
operational denominator. Record per-question validation errors, probabilities,
model identity, reported token usage, and observed transport/provider timing.
No inferential generalization across queries from this one exposed corpus.

For explanatory replay, compare engine/tier starting order and scored order;
never invent original per-engine ranks or call a reconstructed ordering RRF.
The six empty snippets remain empty. Optional policy replays use only saved
responses and require no new calls. There is no automatic adoption decision.

### Reproduction and delivery

The [inert runner](evidence/cardiac-jev-replay-2026-10-04/runner.py.txt) validates
all request hashes offline by default. Live execution requires explicit flags
and a separately supplied private transport; that transport is not committed.
An attempt receipt is persisted before dispatch, preventing accidental retries
of uncertain or invalid attempts. Public responses retain only judgment fields
and sanitized receipt metadata, excluding credentials and transport details.

Publish analysis, call receipts, a plain-English outcome, integrity hashes and a
reviewable PR in this experimental fork. No mainline or service behavior changes.

### Pre-expansion validation amendment after four calls

The three pilot calls passed the original check. The fourth call, W0 first-40
original, stopped expansion on `c21:score`: score 2.88, displayed probability sum
0.99 and weighted mean 2.75 fail the registered absolute tolerance 0.1.
The original receipts remain unchanged in `original-validation/`; this is an
actual initial harness rejection, not a retrospectively passing original check.
No call is repeated or replaced.

Before further calls, root and a Luna reviewer checked an explicit rounding
hypothesis. If each printed probability was rounded to nearest 0.01 and the
underlying distribution sums to one, the feasible weighted expectation is
2.695–2.915; 2.88 lies inside it. The current provider pages describe a weighted
mean but do not guarantee this rounding convention. We adopt this **exploratory
rounding assumption**, not a verified provider contract, in validator v2.
It checks finite values, exact probability keys, normalized-distribution
feasibility under ±0.005 intervals, and score feasibility under those intervals
plus ±0.005 score rounding. It rejects the same distribution with score 4.

All four saved responses qualify under v2 without new provider calls. Derived
receipts retain original validation status/errors and identify the amendment;
original failures remain in the strict-validation denominator. All later calls
use v2, while the frozen questions, input, matrix, budget, and analysis remain
unchanged. If a response fails v2, stop again with no automatic retry. Report
both initial strict coverage and final assumption-qualified coverage.

The amendment was applied offline, before further dispatch: copy the four
original receipts byte-for-byte into `original-validation/`, qualify their
unchanged answers/usage with v2, and write separately derived working receipts.
Only the working receipt's validation metadata changes; no provider response is
changed and no dispatch occurs. The root's initial qualification command is now
reproduced by [qualify.py.txt](evidence/cardiac-jev-replay-2026-10-04/qualify.py.txt).
By default it verifies the original-to-derived relationship; `--write-derived`
reconstructs those four working receipts offline. The runner then sees qualified
existing working receipts and skips dispatching them. The
[qualification audit](evidence/cardiac-jev-replay-2026-10-04/qualification-audit.json)
contains both original/derived hashes and the preserved initial rejection.
The original failure is not overwritten in its sealed ledger.

## Outcome

**The more precise evidence question is worth carrying forward. Simply scoring
all results, or sorting by willingness to fetch, is not the improvement.**
This conclusion is limited to the already exposed cardiac replay. It supports
question-design work, not a runtime replacement or a general quality claim.

All 28 planned requests completed on `jev-1.13.0`, with no new searches or page
fetches. All 28 qualify under the disclosed v2 rounding assumption. The original
validator rejected one response, retained above; replaying that strict score
check across all 28 yields 27 qualifying responses, not 28. There were no provider
HTTP errors, transport timeouts, automatic retries, or replacement calls.
Reported usage was 305,576 input and 24,660 output tokens. Provider round trips
ranged 158–400 ms (median 300 ms); complete private transport ranged 445–831 ms
(median 638 ms). Currency cost was not independently measured. These observations
are not a production latency guarantee. Parallel Luna review did not dispatch
additional provider requests.

### What changed in the top ten

The table reports useful-at-ten under both frozen assistant references; they
agree on these counts. Their finer 2-versus-3 judgments differ, so nDCG is shown
for each separately.

| Request | Useful / 10 | Root nDCG@10 | Luna nDCG@10 |
|---|---:|---:|---:|
| Old question, all 44, original order | 9 | 0.7866 | 0.8495 |
| Old question, all 44, reverse order | 5 | 0.5227 | 0.5675 |
| Old question, all 44, shuffled order | 8 | 0.7867 | 0.8642 |
| New evidence question, all 44, original order | 10 | 0.9225 | 0.8593 |
| New evidence question, all 44, reverse order | 10 | 0.9173 | 0.8268 |
| New evidence question, all 44, shuffled order | 10 | 0.8738 | 0.8506 |
| Old question, first 40, original order | 10 | 0.8210 | 0.9217 |
| New evidence question, first 40, original order | 10 | 0.9202 | 0.8553 |

All eight W1 evidence-first rankings—six matrix requests across both pool
sizes and three orders, plus two exact all-44 repeats—retained ten useful
cards. The four total exact repeats across the study span W0 and W1. The old question ranged from five to ten. For all-44, reversing or
shuffling retained nine of the new question's original top ten, versus six/eight
for the old question. Exact repeat top-ten overlaps were 1.0 for original order
and 0.9 for shuffled order under **both** contracts; neither yielded identical
scores. This distinguishes presentation sensitivity from repeat noise, but one
reverse/shuffle trial cannot isolate a universal causal order effect.

The better useful-card count is not a universal nDCG win: the Luna reference
prefers the old first-40 original ranking (0.9217 versus 0.8553). W1 changes the
scale and includes an additional judgment, so this does not isolate wording
alone. No uncertainty interval over 44 correlated cards is used to pretend we
have multiple independent research tasks.

### A publication can deserve opening without supplying evidence yet

Candidate `c43`, the JACC review at original rank 44, illustrates the distinction.
With the old all-44 original question it reached rank 5; reversing the inputs
put it first. The new evidence question assigned 1.12/3 and rank 29 in the
original order, while the independent fetch judgment assigned `P(fetch)=0.99`.
That is coherent: the card identifies a relevant publication, but does not
present its findings. All six empty-snippet records stayed around the topical
identification level under W1 and none entered its evidence-first top ten.
Neither result establishes that the underlying publication lacks useful evidence.

No useful tail card entered any top ten under either assistant reference. This
corpus therefore still does **not** demonstrate a benefit from scoring beyond
40. Its four tail cards are sparse metadata leads, and the original study had
only one natural above-40 pool. The data are preserved rather than replaced by
new paid searches.

### Fetch priority is a different and less settled judgment

Sorting W1 by `P(fetch)` first did not produce consistently better visible
information: useful-at-ten ranged from six to ten across all-44 orders, and
original order promoted the sparse JACC card again. That does not prove the
fetch judgment wrong; the references grade visible evidence, not actual results
of opening pages. In the original all-44 response, 35/44 cards were categorized
as fetch, seven as maybe, and two as skip. This is broad triage, not a sharp
relevance ranking, and tied high probabilities depend on the specified fallback
ranking keys.

On twelve singleton diagnostics, evidence-score mean absolute change was
0.0186 on a normalized 0–1 scale. Fetch probability mean absolute change was
0.1842 (maximum 0.38). Four empty-snippet cards (`c20,c21,c25,c27`) switched from
maybe in shared state to fetch alone. The neighboring context/question bundle
can therefore change the fetch decision. Do not choose a production fetch
threshold from this replay or treat those probabilities as calibrated utilities.

### Recommendation and limits

Carry forward **separate judgments for visible evidence and retrieval priority**.
The former is a promising evidence-ordering candidate on this replay. Keep the
latter advisory until its request shape and actual post-fetch value are assessed.
Retain the current deployed policy: these observations do not qualify a runtime
change. A later adoption study would need new, independently assessed tasks and
complete downstream outcomes; it is not performed or automatically authorized
here. No medical claims were verified or advice generated.

The [machine-readable analysis](evidence/cardiac-jev-replay-2026-10-04/analysis.json)
contains every trial, ranking, reference metric, facet coverage, tail exposure,
source-host concentration, order comparison, repeat and singleton comparison.
The corpus contains 16 URL hostnames; PubMed accounts for 12/44 cards. Hostnames
are descriptive provenance, not work identities, independent study counts, or
quality weights. Related publications/publisher families and exposed assistant
labels limit generalization. All policy sorting is deterministic and replayable;
no engine-specific rank information was invented.

## Offline verification

Copy `runner.py.txt`, `analyze.py.txt` and `qualify.py.txt` to local `.py` files
if desired; Python can also execute the `.txt` files directly. With the evidence
directory supplied as `PACKET`:

```sh
python3 "$PACKET/runner.py.txt" "$PACKET"
python3 "$PACKET/qualify.py.txt" "$PACKET"
python3 "$PACKET/analyze.py.txt" --self-test
python3 "$PACKET/analyze.py.txt" "$PACKET"
python3 "$PACKET/verify.py.txt" "$PACKET"
```

These commands are offline. The verifier checks corpus identity, request hashes,
unchanged card text/membership, exact repeat bodies, all 28 response validations,
strict versus v2 coverage, both reference ranking metrics, and the original-to-
derived receipt audit. It does not establish independent correctness of labels.
The integrity manifest hashes all published packet artifacts except itself.

Tracking: [issue #416](https://github.com/magnus919/groktocrawl-x/issues/416) and
[the frozen cardiac milestone](https://github.com/magnus919/groktocrawl-x/milestone/15).
This completed bounded study is separate from the completed W0–W13 roadmap.
