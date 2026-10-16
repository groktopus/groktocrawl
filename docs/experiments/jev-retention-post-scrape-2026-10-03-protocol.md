# Post-scrape contribution retention evaluation (2026-10-03)

Status: **prospective protocol; freeze before any new Jev request**. This
evaluates the existing opt-in ADR-0090 path on the current search baseline; it
does not propose activation or a new threshold.

## Question and estimands

On identical current-upstream search snapshots and acquired public text, does
the implemented Jev filter at its existing `0.10` threshold remove any
independently labeled required contribution, and does it change supported
facets or citations in a complete-text synthesis? Separately, what evidence is
lost by the keyless `SourceArtifact.to_document()` 8,000-character projection?

The unit for recall is a labeled contribution passage/facet, not a URL or
successful scrape. Distinct URLs that identify the same paper/version are one
work when explicit identifiers support that identity. Missing identity is
unknown; titles alone do not establish a merge. Failed, refused, or empty
scrapes are recorded as acquisition outcomes, never labeled irrelevant.

## Frozen input and labeling

Use only the shared current-baseline snapshots supplied for this run. Record
the actual deployed SlopSearX revision and successful Jev rerank explanation
separately from reviewed source revisions. Preserve the complete returned
order, result metadata, engine outcomes, available DOI/PMID/PMCID/arXiv and
version relations, search charge, cache state, and every distinct returned
URL. Attempt every eligible URL; record why any attempt could not complete.
Do not issue replacement searches to make a batch look complete.

For each acquired page, record a content hash, byte/character length, and
stable offset spans for: (a) a concrete contribution to a named query facet,
(b) complementary evidence, (c) contradiction or premise challenge, (d)
correction/version qualification, and (e) injection/safety observations.
Contribution, source trust, factual correctness, and safety are separate
labels. An uncertain label remains uncertain. Labels and span references must
be frozen before viewing Jev outputs. Prefer independent review of contribution
labels; if a second reviewer is unavailable, label them best-effort and do not
call them gold labels. Record disagreements and adjudication.

Cover all page text for Jev using the existing overlapping chunker, or record a
provider/model input barrier explicitly. Never silently substitute a prefix.
Include a late-passage control whose required span starts after character
8,000. Label complementary and contradictory sources before scoring. The
existing 2026-09-21 OAuth and Kubernetes packets are calibration/history only;
they are not held-out validation for this protocol.

The implementation takes the maximum score across a page's chunks. Record each
page's chunk count and report retention/misses by chunk-count stratum as well as
overall; pages with more chunks have more opportunities for a spuriously high
maximum. Do not interpret the page maximum as a calibrated probability that
the whole page is useful.

## Paired arms

Freeze the query, full acquired corpus, synthesis prompt, synthesis model and
settings, and run order before synthesis. Replay all arms on the identical
fixed packet:

1. **Full text, no Jev:** every successfully acquired page, complete Markdown,
   without Jev score metadata.
2. **Full text, scored, no removal:** every page in complete Markdown with
   existing Jev score metadata. This control separates score-metadata effects
   from the filter's source removal.
3. **Full text, existing Jev filter:** same complete Markdown and metadata,
   omitting only pages the current `0.10` rule omits. Provider failures and
   incomplete assessments retain the source, as in the implementation.
4. **Keyless 8k projection:** every successfully acquired page with the
   keyless default 8,000-character projection and no Jev metadata. This is a
   separate projection comparison, not evidence for or against Jev.

Use identical synthesis instructions/model/settings for all arms; no answer
length cap or source-count cap may differ by arm. If the complete-text input
exceeds a documented provider context limit, mark synthesis blocked for all
full-text arms and preserve that fact; do not clip text to force a run. Do not
introduce citation verification into this experiment: audit whether cited
claims are supported by exact spans, but distinguish this outcome from a
formal verifier.

## Outcomes and analysis

Report returned/acquired/failed counts and reasons; unique works and versions;
Jev calls, chunks, valid scores, failures, latency and spend; and, per arm,
whether every pre-labeled contribution reaches the synthesis input. Compute
required-contribution and facet recall, with all misses enumerated. Audit
complementary and contradictory evidence explicitly. For each answer, report
facet coverage, cited source/work coverage, exact cited-span support, and
unsupported or contradicted claims. Keep per-batch results; do not pool away a
miss on one query. Report elapsed time as descriptive because one replay per
arm is not a latency comparison.

Do not fit a new cutoff on validation data. The initial assessment uses the
existing `0.10` threshold as-is. If a distinct calibration set is later used
to select a different rule, freeze that rule and its code before acquiring or
labeling validation cases; this protocol's current validation results cannot
be reused to tune it.

## Operational limits and disposition

No new searches are authorized in this lane; use the snapshots supplied by the
coordinator. Bound external Jev requests to 50 initially, one in flight in
this lane, with model `jev-1.13.0`. Do not read credentials into output or
persist them. Stop before exceeding the request bound; label unscored pages
unevaluated and keep them in synthesis. Preserve failed-call evidence in the
private ledger and publish only sanitized metadata/aggregates.

This is a research spike. No deployment, activation, threshold change,
mainline change, or architecture commitment follows from a result. The final
report must make a go/revise/no-go recommendation for further evaluation,
state sample limitations, and identify every lost required contribution.

## Existing implementation under evaluation

The current experimental path is `agent-svc/agent/research/loop.py` calling
`agent-svc/agent/research/jev_filter.py` after acquisition. It uses pinned
`jev-1.13.0`, overlapping 100,000-character chunks, maximum chunk score, and
the configured `0.10` inclusion boundary. Provider errors, invalid answers,
private-host skips, and incomplete assessments keep a source. The filtered
arm renders retained artifacts with `max_chars=None`. The keyless document
path defaults through `SourceArtifact.to_document()` to 8,000 characters.
This evaluation covers that existing path only; it does not change these
runtime behaviors.

## Freeze receipt

Protocol SHA-256 and signed-off commit are recorded in the run ledger after
this file is committed. No new Jev calls may precede that commit.
