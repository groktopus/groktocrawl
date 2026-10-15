# TypeSafe Jev shadow evidence-router protocol

Status: **bounded comparison complete; passage-triage integration deferred.
See [outcome](outcome.md).**

## Arms

- **Control:** the acquired source artifact continues through the incumbent path
  without a Jev decision. Existing retrieval order and deterministic barriers are
  unchanged.
- **Shadow treatment:** Jev reads the fixed query and passage and returns one route
  plus four independent probabilities. The result is recorded but cannot affect
  the control output.

Both arms use the same acquired text. Acquisition differences are not part of the
independent variable. This experiment does not change SlopSearX.

## Question contract

The frozen adapter asks one `Choice` question with these routes:

- `evidence`: useful support for the query;
- `conflict`: relevant evidence that challenges a premise or likely answer;
- `irrelevant`: no material help;
- `unsafe`: instructions aimed at manipulating an AI system;
- `uncertain`: insufficient or ambiguous state.

The same request asks four `Noul` questions: material relevance, usable evidence,
query contradiction, and prompt injection. Questions are independent and share the
same state. Code validates the complete route distribution and never repairs an
invalid response.

## Preflight corpus

`exposed-routing-corpus.json` contains twelve public synthetic cases across direct
support, independent corroboration, contradiction, topical near misses, unrelated
text, explicit injection, tool manipulation, ambiguous fragments, identity
ambiguity, derivative evidence, and relevant-but-empty text.

These cases are exposed calibration and transport fixtures. They cannot be called
held out and cannot establish product value. A private technical-smoke freeze
was recorded before using them with a credential; the provider-specific readout
remains outside the public repository. A separate scenario corpus, labels, and
calibration/validation split were frozen for a matched synthetic comparison.
Its first run stopped during calibration. A separately frozen execution using
the same cases completed validation. A fixed rule selected there was then
applied to a separately frozen corpus of public first-party GroktoCrawl X
document excerpts, after the account owner confirmed published terms apply.
The owner then identified the synthetic-only cutoff as an important limitation.
A second real-document set, labeled before model calls, was run to examine
score separation and nominate a new candidate cutoff. Both real sets are now
calibration data; neither can validate a cutoff chosen after seeing them.
A real-data candidate was then frozen before a new packet was acquired through
GroktoCrawl's public web search and scraper. That packet mixed topical source
excerpts, cross-topic distractors, a false premise, unanswerable material,
and one explicitly synthetic injection control. The predeclared combined gate
failed on the separately frozen validation packet, so no end-to-end answer
comparison followed under that protocol. The owner identified combining
injection classification with evidence value as a harness-design error. The
relevance observations and safety-policy observations must be interpreted
separately. The search with no web results and scrape failures remain in the
private acquisition ledger. A follow-up relevance-only packet used two new
GroktoCrawl search result lists in returned order, with first-6k scraped
passages and labels frozen before Jev calls. It added no cross-topic
distractors. The fixed exclusion rule made no source-set change in either
repeat, missing its practical-effect gate; see the [outcome](outcome.md).

## Required private freeze

Before the first live call, freeze:

- Jev versioned model ID, endpoint contract, questions, and code revision;
- case IDs, input hashes, strata, references, repetitions, and work order;
- independent reviewer and adjudication procedure;
- practical-effect threshold, maximum source-recall loss, latency/cost ceiling,
  missing-result treatment, and launch exclusion rules;
- authorized input data classes, retention location, spend ceiling, and permitted
  publication surface;
- a statement that prior SlopSearX observations are external motivation rather
  than a comparison arm or GroktoCrawl result.

## Measurements

Evidence-value measurements:

- macro and per-stratum route accuracy;
- false exclusion risk for required evidence and contradictions;
- uncertainty on incomplete or ambiguous passages;
- supported material claims and citation closure in the unchanged control answer.

Separate safety-policy measurements:

- unsafe-instruction detection and false positives against labeled controls;
- deterministic harness precedence when relevance and injection signals conflict;
- fallback or quarantine behavior for missing and contradictory model receipts.

A deterministic research-only harness check decided what to do with frozen
scores; that does not make
the model's injection classification deterministic or prove a flagged passage
malicious. A safety alert must not become evidence of irrelevance, and relevance
must not override a safety quarantine.

Operational:

- p50/p95 incremental latency;
- requests, input/output tokens, and cost per completed research request;
- fallback, overload, rate-limit, timeout, and invalid-response rates;
- model-version identity and sensitivity;
- exact equality of keyless and provider-failure user-visible behavior.

Report case distributions and all failures. A missing result is not a favorable
classification. Do not infer calibration from confidence alone; measure it against
the frozen references.

## Execution

The exposed fixture can be exercised without a key:

```bash
UV_CACHE_DIR=/tmp/uv-cache PYTHONPATH=agent-svc:. \
  uv run --no-sync python scripts/run_typesafe_shadow_spike.py \
  --output /tmp/typesafe-shadow-preflight.json
```

Every record should be `skipped/key_absent`, proving that the runner makes no
network call and retains no decision without explicit credentials. A live run uses
the server-side `TYPESAFE_API_KEY` and writes to a private path. Never commit the
live ledger before legal/publication review.

## Stop rules

Stop immediately if data governance is unresolved, the key or raw response can
enter logs/artifacts, the adapter influences user-visible output, the cost ceiling
is reached, more than 10% of a launch ends in terminal provider failure, or keyless
equivalence fails.

Complete the planned repetitions and then stop. The original combined gate is
retained as historical protocol, not silently re-scored as a pass. Its
relevance and safety components are interpreted separately. Passage triage
must clear quality, recall, latency, cost, and failure gates before evaluating
reranking or citation checking. A negative result is retained.

## Architecture disposition

No new ADR is proposed. The code is a bounded experimental adapter, not an
architecture selection. Real-data calibration corrected the synthetic-only
cutoff. The subsequently frozen combined gate failed, but its relevance
component passed. A separate ranked-result relevance-only packet missed its
practical-effect gate. The safety-classifier failure alone did not reject
evidence value; the final no-ship decision rests on the latter packet's lack
of measured gain. See the [outcome](outcome.md).
