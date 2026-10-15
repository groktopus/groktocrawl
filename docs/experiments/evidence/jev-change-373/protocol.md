# #373 public revision-pair freeze

Status: **case and label freeze prepared for parent review; no Jev calls made**.

This packet extends the previously frozen continuation/monitoring protocol only
for the #373 public revision-pair study. The earlier six-case monitoring pilot
remains a historical, separate sample and is not pooled with this set. This is
an offline, shadow-only study. No live monitor, notification, or deployment is
changed.

## Question and unit

For a deterministic, content-addressed before/after page revision and one
explicit saved intent, does the confirmed textual change warrant a notification
for that intent? The unit is one revision-pair × intent. Two public diffs are
repeated under two distinct intents to measure intent sensitivity. The model
never decides whether a page changed: the frozen snapshot hashes and diff are
authoritative.

The comparator is the existing deterministic page-change signal: every
successfully acquired pair with distinct page hashes is a changed-page event;
failed acquisition is unavailable and skipped. Jev may produce a shadow
significance judgment only. It cannot suppress or create a change event or
notification.

## Frozen System One judgment

- Model: `jev-1.13.0`, System One, one sequential request per registry case.
- Typed output: one Noul proposition probability in `[0,1]` for the “yes” answer
  to the exact question stored in `request-packets.json`. The number is a
  probability for that proposition; it is not a separate confidence field.
- The state contains only the explicit saved intent and bounded exact before/after
  source excerpts recorded in `evaluation-excerpts.json`, with their original line
  spans. It contains no URL, full page, unified diff, expected label, split name,
  source reputation, or case identifier. Complete pages and diffs remain evaluator-side.
- A fixed `p >= 0.50` converts a validated Noul to suggested `material`; lower
  values suggest `immaterial`. This is a prespecified descriptive cut, not a
  tuned threshold. A best-effort `uncertain` reference case is excluded from
  binary error-rate and Brier denominators and reported separately.
- There are 11 planned calls: 4 calibration and 7 validation. Each is attempted
  at most once, with one request in flight and no retries. The hard ceiling is
  16; the five-call remainder is not reassigned after outcomes. The synthetic
  failed-acquisition control has zero planned provider calls.
- Calibration cases are excluded from validation metrics. The calibration
  split may detect transport/response-schema problems only; it cannot change
  wording, examples, labels, preprocessing, split, model, or decision cut.

## Frozen strata and reference labels

The public registry contains 11 revision-pair × intent cases over nine genuine
upstream changes: Kubernetes website, CPython documentation, and Sigstore Cosign.
The strata cover version/status, substantive semantic changes and removals, a
scope qualifier with an intentionally uncertain intent, formatting-only
cleanup, a factual documentation correction, air-gap operational guidance,
navigation metadata, and two same-diff/two-intent controls. For every pair, the
before revision is the immediate GitHub parent of its after revision. GitHub API
verification is performed against the after commit and confirms both its parent
SHA and changed path; the before row records this relationship, not a separate
changed-path assertion. Every raw snapshot is preserved with byte length and
SHA-256 under `pages/`; complete deterministic diffs are preserved under
`diffs/`; only bounded exact excerpts in `evaluation-excerpts.json` enter model
state. Source URLs, path identities, hashes, license metadata, and verification
date are in `source-snapshots.json` and `case-registry.json`. License names/SPDX
identifiers reflect GitHub API metadata. Repository license files are linked;
root NOTICE status was checked separately. CPython remains `NOASSERTION`. Source
snapshots are unmodified; excerpts and diffs are derived study artifacts.

`reference-labels.json` is a best-effort assistant assessment made before any
Jev result. It is not independent gold. The uncertain qualification case is
retained and reported, but is excluded from binary scoring. Parent review must
occur without Jev outcomes and any label change must be committed before calls.
The same `cosign-link` diff is evaluated once for contributor-onboarding intent
and once for cryptographic-verification intent. The same `k8s-nav` diff is
also evaluated for technical-content intent and navigation-curation intent.
These are separate units sharing exact diff bytes; the paired comparisons are
reported as within-diff intent sensitivity, not independent samples.

## Failure and scoring

The failed-acquisition control is a synthetic deliberately invalid path at a
valid public commit. Its recorded HTTP 404 is a test input, not a public-page
revision. The deterministic action is `skip_without_provider_call`; it is
labeled `uncertain`, never immaterial, and excluded from all model denominators.

For validation, report exact counts and denominators for false `immaterial`
recommendations on material references (primary miss risk), false `material`
recommendations on immaterial references (avoidable noise), fixed-cut accuracy,
raw Noul Brier score on determinate validation references, uncertain responses,
provider/transport/schema failures, latency, and provider usage/cost if returned.
Report calibration results separately. With seven validation requests and
source- and intent-clustered cases, metrics are descriptive; no population
significance, calibrated production probability, or threshold optimality is
claimed. Failure to obtain a model judgment remains unevaluated; deterministic
change reporting remains the fallback.

Provider credentials remain in the authorized process/proxy only. Full provider
receipts stay outside version control. Public artifacts contain frozen inputs,
source hashes, and later sanitized aggregates only. No prompt text is printed
with live credentials, and there are no retries or concurrent calls.

## Review gate and interpretation

This commit freezes sources, input requests, labels, and scoring before any
provider response. Parent review of `case-registry.json`,
`reference-labels.json`, `request-packets.json`, and this protocol is required
before calls. A later call-result commit must record the exact freeze commit,
all 11 planned statuses, input digests, response validation, and raw private
receipt location without publishing private receipt contents. Results can only
support revise/no-go/go for continuing bounded shadow evaluation; they cannot
authorize a runtime gate, notification suppression, deployment, or mainline
change.
