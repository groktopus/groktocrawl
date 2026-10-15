# Real-model research pilot

The full replacement roadmap is authorized under [issue #103](https://github.com/magnus919/groktocrawl-x/issues/103).
This slice connects a question to source acquisition, unverified knowledge
construction, batched model checks, three reports and an exact deterministic render audit.
It is an experimental developer runner. Public API/CLI/MCP delivery, retained
model publication, targeted follow-up and comparative evaluation remain unfinished.

## Run the pilot

Configure `SEARXNG_URL`, `SCRAPER_URL`, `LLM_BASE_URL` and `LLM_API_KEY` in the
process environment for reachable services. Credentials must never be committed.
The runner requests the owner-selected LiteLLM alias `local`:

```sh
PYTHONPATH=.:agent-svc .venv/bin/python scripts/run-research-pilot.py \
  'What evidence supports enterprise agentic software-factory productivity claims?' \
  /tmp/my-new-real-research
```

The directory must be new. The runner performs one search, acquires at most three
sources using lightweight scraping, and fails on acquisition barriers, warnings or
empty content. The query deadline is 180 seconds; cancellation is cooperative.
There are no automatic retries or alternate-model fallbacks. A successful candidate
uses two model calls: construction and one ordered review batch. The model-call ceiling
remains 64, with separate request/response byte and output-token-request limits. Actual
provider usage is observed rather than assumed to equal the requested ceiling.

After successful checks, it writes `summary.md`, `analysis.md`, `dossier.md`,
`knowledge.json` and `manifest.json`. The manifest is written last. `usage.json`
records dispatched model calls, reported model/token metadata, raw response digests
and failed/uncertain calls. Missing usage stays unknown, not zero. On failure there
is no successful manifest; previously dispatched work is not silently retried.
These files are not a retained PostgreSQL publication or a quality-comparison result.

## Model work and server-owned identity

`construct_research()` accepts captured sources from trusted acquisition callbacks.
The internal `research-construction/4` draft under policy `real-research-pilot/3` asks the model for up to three dense
source-statement claims, source-line selections, support/contradiction selections,
answer status and conflict descriptions. References are one-based positions in
bounded arrays. The model does not create scope, research, revision, evidence, claim,
question or graph-edge identifiers. The server assigns those and builds the existing
consolidated knowledge records.

The server extracts exact source lines and computes character offsets and hashes.
Repeated text is unambiguous because its line range identifies the occurrence.
Out-of-range, repeated or foreign references fail validation. Publication/effective
source dates remain unknown; capturing a document cannot establish current truth.
The initial policy supports statements about captured documents with historical
scope. Current-freshness checks cannot pass from unknown dates. Construction returns
unverified knowledge, never model-authored verification or human approval.

`ModelReviewAdapter` validates exact source bytes and sends the frozen context once
for an ordered batch of structural, conflict/coverage, assessment, support and
freshness decisions. The gateway constrains the response schema; the server requires
every check index in order, validates each check-specific outcome, and binds accepted
decisions back to their full input digests. The existing ledgers still execute every
individual check. A deterministic renderer creates the three report layers from
assessed claims. A separately identified tool auditor regenerates all three layers
and requires every output byte and descriptor to match before publication eligibility.
Required negative or indeterminate judgments prevent successful publication eligibility.

`ConsolidatedJourney` accepts registered model/tool reviewers. The compatibility
entry point `ConsolidatedFixtureJourney` still rejects non-fixture reviewers, and
the fixture-only PostgreSQL writer is unchanged. Returned candidates are ephemeral;
closed execution owners cannot authorize a later retained commit.

## Transport and provenance

`ReviewTransport` uses a server-configured OpenAI-compatible endpoint and key with
an existing `httpx.AsyncClient`. It refuses redirects, bounds response bytes, and
rejects truncated, refused or tool-call completions. Provider error details are
suppressed from returned errors. Failed dispatches consume review call slots;
closed or cancelled owners reject late decisions.

Some compatible gateways return one complete Markdown `json` fence despite a JSON
request. The transport unwraps only that exact whole-response envelope and retains
the original content digest. It does not extract JSON from surrounding prose,
repair malformed JSON or change judgment fields. Strict canonical/schema admission
still runs afterwards. Prompt and generation configuration have explicit model
reviewer provenance. Model review is not independent human review or calibrated truth.

## Gateway evidence and current limits

Read-only inspection of `incumbent.example.internal:docker-compose/groktocrawl` confirmed the existing
endpoint `https://research-provider.example/v1` and configured authentication. A bounded
`local` completion succeeded with finish reason `stop`, reporting model `local`.
The mounted LiteLLM configuration on `inference.example.internal` maps `local` to
`openai/Carnice-Qwen3.6-MoE-35B-A3B-APEX-MTP-I-Nano.gguf`. Record configuration and
returned identity separately: the response alias alone does not prove the backend.
Neither production deployment nor gateway configuration was modified.

Development probes found and retained failures for JSON code fences, paraphrased
quotes and invalid model-generated references. The transport envelope handling and
server-owned line/index mapping address those mechanical failure modes. These are
public-source development diagnostics, not a held-out or scored evaluation. Passing
structural tests does not establish research quality. Full live journey outcomes,
failed trials and later comparative evidence must be reported separately before
claiming improvement over the incumbent.

### Latest development result

The schema-constrained transport returned an allowed label in a bounded live `local`
probe. In a complete synthetic-source journey, construction completed in 8.19 seconds
and the ordered semantic batch completed in 44.87 seconds. The former model render
audit then timed out at 90 seconds. That redundant model audit has been replaced by
the exact deterministic audit described above. A later run varied: construction took
24.62 seconds and the batch timed out at 90 seconds. The gateway then returned HTTP
502 even for a one-field schema probe, and direct SSH to `inference.example.internal` timed out.

These retained failures show that invalid-label generation is addressed, but the
candidate is not frozen or live-ready. No complete manifest has yet been produced,
and observed latency already exceeds the proposed W1 bound. The next probe must wait
for a healthy local backend, exercise the two-call candidate without retries, and
record the terminal outcome. A successful run would establish only functional
viability; comparative quality and replacement claims still require the fresh blind
packet and paired study.

### Policy 3 probe result

Policy `real-research-pilot/3` states the required construction document identity explicitly in the model instruction. A bounded fixed-source probe through `local` then passed construction and completed the ordered review call, where the publication gate denied the candidate because the model did not pass the required structural/conflict checks. A separate live-source probe completed both model calls and was denied because a rendered claim was ineligible. These are terminal fail-closed outcomes, not successful manifests. They confirm that the schema-identity correction reaches the review and publication stages; they also show that model-judged structural checks are now the next candidate-design question. A later fixed-source probe with application-owned structural and conflict checks completed both model calls and produced all three reports plus a canonical manifest. The retained [probe packet](evidence/w1-candidate/2026-09-10-deterministic-checks/README.md) establishes candidate-core functional viability only. Candidate B can now be pinned for fresh-packet curation; live search acquisition and comparative quality remain open.
