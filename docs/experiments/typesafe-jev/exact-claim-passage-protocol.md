# Exact claim–cited passage Jev evaluation

Status: frozen before provider calls in commit `2c8a7b9` (packet digest
`2a2e2fba2f99230702beed7d1c6e886544cd9b9fc51f1f0a0df0506113d60d12`; labels
digest `b478f92e46ea91c441a5904c3c744d5b8c1f2120e52cd7469533470167b215b8`).
Results and post-call limitations are reported separately.

## Question and boundary

Does pinned `jev-1.13.0` add useful evidence about whether one exact cited public
passage supports one exact generated claim, beyond the existing deterministic
citation/quote checks and the current semantic-verification path? This is a
private, low-risk, shadow comparison. The shared search snapshots contain no
unchanged synthesis answer, so this bounded corpus uses constructed claim variants
grounded in acquired public pages. It is a controlled classification pilot, not a
replay of production answer claims or an end-to-end answer-quality test. Jev cannot
change an answer, admit or remove a source, recommend publication, or affect a
user-visible path. A missing, invalid,
or hard-negative result never becomes `supported`.

## Unit and labels

The unit is one pair: the exact claim text as generated, with all dates, versions,
quantifiers, conditions, and scope qualifiers intact, and the exact cited passage
as acquired. Retain its public URL, title, source work/lineage, version, retrieval
time, and exact byte/character offsets and digests. Do not silently widen a passage
or repair a claim. The same pair is sent to every arm.

Assign one pre-call reference label:

- `supported`: the cited passage directly entails the full claim, including every
  material qualifier.
- `contradicted`: the passage states a material incompatible fact or qualifier.
- `related_insufficient`: it is on topic but does not entail the claim; includes
  background, adjacent facts, and a claim missing a necessary condition.
- `unverifiable`: passage/source identity, version, or wording prevents a stable
  judgment from this pair. This is not a synonym for merely difficult.

Record a short rationale and exact decisive subspan. Reviewers see no model outputs,
arm outcomes, or threshold. Assistant-proposed labels must be identified as such;
they are not independent gold. Resolve disagreements before unblinding; if unresolved,
retain `unverifiable` and report the disagreement.

## Sampling and leakage control

Build this controlled candidate frame from the shared, already-acquired public
search/scrape snapshots. The snapshots do not preserve unchanged generated
answers, so claims here are explicitly constructed variants grounded in those
pages; this is not production answer replay. Do not search or scrape to replace
a hard case. Include support, contradiction, related-but-insufficient,
unverifiable cases, version claims, qualifiers, and same-work mirror claims.
Mirrored copies are one work, not independent corroboration; record lineage and
version explicitly. No corrected historical capture was available, so this
corpus does not test correction chronology.

Group all pairs from the same claim family, source work/lineage, and near-duplicate
passage family before splitting. Put whole groups into calibration or validation;
never place a mirror, paraphrase, adjacent version, or rewritten claim from one
group on both sides. Freeze the validation labels and digest before calls. Do not
use validation outcomes to choose or revise the threshold. Keep model-assisted
calibration separate from any later validation readout.

## Arms and masking

1. **Deterministic incumbent controls:** run the existing citation identity,
   exact-quote/source-span checks and current hard-negative policy unchanged.
   Record structural eligibility separately from semantic support; structural
   validity is not evidence of entailment.
2. **Current semantic-verification control:** adapt the W12.3 semantic
   verification prompt to one exact evidence span and run on the same pair. The
   control uses the authorized `free` proxy alias because this isolated study
   does not have the production `local-litellm` route; this is a contract
   comparison, not a replay of the production model/route. Record this limitation
   with all results. It receives no reference label, Jev result, arm name, or
   hidden rationale.
3. **Jev shadow arm:** one request per pair to `jev-1.13.0`, same claim/passage and
   public source metadata, with a frozen single-choice classification:
   `supported`, `contradicted`, `related_insufficient`, or `unverifiable`. Treat
   quoted/source text as data, never instructions. Do not expose labels, control
   results, or hidden context. Retain only a private, content-free response receipt
   plus the exact input digest; keep raw public text in the approved local snapshot.

Any existing hard-negative barrier (identity mismatch, wrong version, same-work
mirror presented as independent, missing exact citation binding, or incomplete
qualifier coverage) overrides every arm. The harness can combine arms for analysis,
but only records a shadow outcome; it cannot authorize publication.

## Calibration, validation, and analysis

Freeze prompts, model/version, code revision, corpus, group split, labels, work order,
retry policy, timeout, cost ceiling, and metrics before the first provider call.
Use calibration only to choose at most one probability/confidence threshold and one
fixed combination rule. Then apply both unchanged to the separately frozen validation
set. If the sample is too small for a defensible threshold, report descriptive
results and choose no threshold.

Primary risk is the false-supported rate: non-supported reference pairs classified
or promoted as supported divided by all non-supported pairs; report counts and
denominators, including contradicted, related-insufficient, and unverifiable strata.
Also report supported recall/false rejection, per-label confusion, hard-negative
failures, disagreement cases, invalid/missing responses as conservative failures,
latency, request/token/cost totals, and whether any label would alter an eventual
cited answer under an offline replay. Report intervals clustered by claim family /
source lineage where feasible. Separate Jev confidence calibration (multiclass
Brier score and reliability bins) from the semantic verifier's confidence; do not
claim calibration when counts are inadequate.

## Limits and stop rules

Target no more than 40 initial Jev calls and one in-flight Jev request. Stop at the
budget; do not replace failed pairs or retry with changed inputs. Provider failure,
invalid schema, wrong returned model, or digest mismatch is retained as failure and
preserves existing behavior. Stop before a call if labels, source terms, provenance,
privacy, or lineage are unresolved. Do not publish private receipts or credentials.

The issue can conclude go/revise/no-go for another bounded research step only. It
does not approve runtime integration, deployment, publication changes, or a Jev-
controlled decision. Any such change needs a separate decision and review under
ADR-0085.
