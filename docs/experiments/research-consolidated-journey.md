# Consolidated research journey

The bounded experimental journey now connects source acquisition, knowledge
construction, executed checks, three report layers, executed render audit and the
publication candidate gate. This implements the end-to-end fixture portion of
[issue #94](https://github.com/magnus919/groktocrawl-x/issues/94).
The optional storage integration commits the result in an isolated PostgreSQL
transaction; the command below remains a file-only demonstration.

Start with the generated [summary](consolidated-example/summary.md), then the
[analysis](consolidated-example/analysis.md) or [dossier](consolidated-example/dossier.md).
These use **fictional enterprise software-factory notes and authored judgments**.
They demonstrate the architecture's behavior, not research findings or improved
semantic quality. The example deliberately leaves enterprise-wide productivity
unresolved, while reporting what the fictional pilot says. Every report preserves
partial coverage, the limitation on causation, and fixture labeling.

To generate fresh reports and exact canonical knowledge/manifest JSON in a new directory:

```bash
PYTHONPATH=agent-svc .venv/bin/python scripts/run-consolidated-research-fixture.py /tmp/my-new-research-fixture
```

The command refuses an existing output directory. It makes no network or provider
calls. Each execution generates new check/audit identities, so canonical record
digests can differ between runs; the authored report text is deterministic.

## What runs

`ConsolidatedFixtureJourney` accepts one root context, exact check inputs, source
acquisition callbacks and configured fixture verifier/renderer/auditor callbacks.
It validates the input budget, acquires exact source bytes, resolves all evidence
spans, executes checks, builds separate assessment links and introductions, and
admits canonical checked knowledge. It then invokes the renderer, validates the
three outputs, executes an audit over their actual bytes and pinned knowledge,
and calls `prepare_publication()` with source/history revalidation and both live
execution owners. Mixed assessment outcomes require explicit adjudication; the
journey does not choose a favorable outcome automatically.

`consolidated_example.py` supplies a zero-spend authored scenario. Its auditor
compares entire report bytes with expected fixture outputs, including caveats
outside statement mappings. That deterministic check is useful for testing the
plumbing; it is not an independent semantic auditor.

The result includes canonical knowledge/manifest bytes, exact source/output bytes
and an explicitly fixture-only candidate. Execution owners close on success,
failure or cancellation. The returned data is not a portable authorization token;
the retained integration performs its fenced commit inside the live workflow
before those owners close.

## Bounds and remaining work

One journey runs once, with no retries, parallel acquisition or provider adapter.
It accepts 1–97 sources, 1–64 check inputs, exactly three reports and one render
audit. Sources retain the 10 MiB individual bound; accumulated source, report and
canonical document bytes cannot exceed the 100 MiB root bound. Context/check inputs
and final canonical documents preserve the 1 MiB limits. Encoded bundles and scope
quotas remain separate storage responsibilities; this result is not an encoded
bundle or storage reservation. The default cooperative deadline is 30 seconds,
configurable from 1 to 120 seconds. Trusted callbacks must honor cancellation;
there is no process sandbox or forced termination of arbitrary Python code.

Fourteen focused tests cover the full journey, incomplete coverage, single use,
source denial/alteration, actual negative checks, missing output layers, omitted
caveats with consistent hashes, deadline and cancellation suppression. Existing
manifest and legacy format tests remain unchanged.

The bounded retained integration is implemented in PR #101, including root-generation,
liveness and quota fencing, fixture provenance, and actual PostgreSQL round-trip,
lifecycle and restore probes. It supports one initial publication per root;
successor revisions, historical re-render and import/export for the consolidated
format remain unsupported. No production stack change, provider spend, format
freeze or semantic-quality promotion follows from this demo.

The [consolidated storage integration](research-consolidated-storage.md) adds a
server-owned commit hook inside the live execution window, with an isolated
PostgreSQL transaction and explicit receipt reconciliation. The no-storage demo
remains unchanged. See the storage guide for verified hosted evidence.
