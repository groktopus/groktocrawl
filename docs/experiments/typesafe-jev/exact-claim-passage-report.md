# Exact claim–passage controlled pilot results

This is a bounded, constructed-pair pilot, not production answer replay. The
shared search snapshots had no unchanged synthesis answers. The 25 pairs were
constructed from acquired public SLSA-related pages and exact captured spans;
all 25 offsets, source-page hashes, and citation URL bindings validated before
the calls. There were 12 calibration pairs and 13 validation pairs, grouped by
claim family/source work/lineage with no cross-split group overlap. The corpus,
labels, prompt, and Jev question contract were frozen in commit `2c8a7b9` before
provider calls.

The reference labels are assistant-proposed, not independent gold. A second
assistant did a blind review before seeing model results and found one likely
label disagreement: `val-v01-mirror` is labeled `related_insufficient`, but the
claim says the citation is an independent GitHub mirror while its frozen source
URL and lineage identify the official SLSA site and same specification work.
The frozen label was retained for primary reporting; a sensitivity recode to
`contradicted` reduces each arm's exact-label accuracy by one case and does not
change the false-supported count. This review is not human adjudication.

| Arm | Calibration | Validation | Validation exact-label matches | False-supported, validation |
| --- | ---: | ---: | ---: | ---: |
| Jev `jev-1.13.0` | 8/12 | 10/13 | 10/13 | 0/9 non-supported |
| W12.3 prompt contract, free proxy | 7/12 | 9/13 | 9/13 | 0/9 non-supported |

Jev completed all 25 sequential requests with the requested and returned model
both `jev-1.13.0`. The validation confusion counts (rows are reference labels;
columns are predicted `supported`, `contradicted`, `related_insufficient`,
`unverifiable`) were:

| Reference | Supported | Contradicted | Related, insufficient | Unverifiable |
| --- | ---: | ---: | ---: | ---: |
| Supported | 4 | 0 | 0 | 0 |
| Contradicted | 0 | 4 | 0 | 0 |
| Related, insufficient | 0 | 1 | 2 | 0 |
| Unverifiable | 0 | 0 | 2 | 0 |

Jev used 18,996 input and 1,462 output tokens; median latency was 182 ms
(maximum 225 ms). The semantic replay used 5,122 prompt and 1,731 completion
tokens; median latency was 3,150 ms (maximum 7,964 ms). Jev receipts supplied no
billable-cost field. The free-proxy usage metadata reported zero cost under a
NAS model-override rate source, which is not treated as actual billable cost.

The W12.3 verifier prompt was adapted to one exact passage and called through
the authorized `free` proxy alias; both the configured alias and returned model
were `free`. This was not the production `local-litellm` route/model. Its
validation confusion counts were:

| Reference | Supported | Contradicted | Related, insufficient | Unverifiable |
| --- | ---: | ---: | ---: | ---: |
| Supported | 4 | 0 | 0 | 0 |
| Contradicted | 0 | 4 | 0 | 0 |
| Related, insufficient | 0 | 2 | 1 | 0 |
| Unverifiable | 0 | 0 | 2 | 0 |

Both model arms returned `supported` for zero of nine non-supported validation
pairs, and zero of 18 hard-negative pairs overall. On this small, constructed
set, Jev and the semantic control agreed on 23/25 pairs. Their only differences
were `cal-enc-l1-forgery` and `val-v11-start-chain`, where Jev chose
`related_insufficient` and the semantic control chose `contradicted`. Jev's
descriptive multiclass Brier scores against the assistant-proposed labels were
0.559 on calibration and 0.373 on validation; the sample is too small and the
labels too weakly independent to claim confidence calibration. No threshold or
combination rule was selected.

The existing deterministic checks verified source URL, exact passage offsets,
and capture digest for all 25 pairs. Those checks establish input and citation
integrity, not entailment. The `hard_negative` flags are frozen case annotations
used to identify a subset for analysis; this runner did not execute a
deterministic runtime block, nor test whether a model could override one. Thus
the 0/18 model false-supported count is an observed result on annotated cases,
not evidence about runtime gate or fallback behavior. Runtime gate and fallback
behavior remain untested by this pilot. The model result files and complete
input digests are in `exact-claim-passage-results.json`.

The first escalated semantic-control pass exposed a harness/parser defect: it
accepted only lowercase verdict strings and a 0–100 confidence value. A safe
diagnostic call showed a title-case verdict and a value such as `0.98`; raw
completion text was not retained, so the original 18 schema failures cannot be
individually decomposed. The same frozen 25 pairs were replayed with verdict
case normalization and all 25 produced a valid label. The failed first attempt
remains recorded and is not counted as model errors. All returned semantic
confidence values were between 0.90 and 1.00, but the adapted prompt did not
specify the confidence unit. Those values are preserved as ambiguous and are
not used for calibration. One earlier unprivileged proxy attempt failed with
`proxy_transport` before obtaining provider results and is also excluded.

The constructed cases are straightforward and labels are not independent; there
are few source families, no real generated answers, no corrected-history cases,
and no production-route semantic-verifier replay. These results do not establish
incremental field value, reliable false-support performance, or readiness for
runtime integration. Keep Jev in shadow-only research; this experiment makes no
determination about runtime hard-negative enforcement or fallback behavior.
Next evidence should use independently adjudicated unchanged answer/citation
pairs and the actual current verifier route, with a new frozen protocol before
any live integration decision.
