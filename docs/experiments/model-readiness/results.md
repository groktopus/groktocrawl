# Readiness reliability follow-up — September 30

**Readiness repair complete: merged PR #396 was deployed to the experimental agent at `bb5f2a3eb00df73eadb7163c6ed88504ee5cc843`, model `free`.** The candidate-only update changed agent image/revision, preserved other rendered configuration, waited for no active/pending jobs or admission work, and retained the prior image for rollback. Mainline deployment and Hermes configuration were unchanged.

All eleven candidate services were running and healthy after deployment. Browser init remained enabled and the semantic service retained its effective 14-CPU quota. This changed runtime is not the frozen seven-day W9 runtime; completed W9 results remain historical bounded evidence.

## Behavior and verification

One minimal readiness probe retains a five-second wall-clock limit. Slow/indeterminate probes can proceed through ordinary bounded research; unavailable, rate-limited and rejected probes return HTTP 503. The application now preserves `X-LLM-Readiness` on error responses. Cancellation closes the temporary client. No probe retries were added; ordinary generation and publication checks retain their existing behavior.

- Sixty-nine focused LLM/readiness tests passed locally. All twelve new regressions also passed in strict asyncio mode after explicit markers were added to match container CI.
- Final [Runtime CI](https://github.com/magnus919/groktocrawl-x/actions/runs/36788582456) passed 2,224 integration tests (218 skipped, 17 deselected), MCP transport tests, storage/restore/capacity probes and required gates. The earlier test-marker failure remains in public CI history.
- The [live receipt](live-stream.json) records HTTP 200, `readiness=ready`, 46 token events, two sources, one terminal done event and no error event, in 73.46 seconds. The request was fresh, focused and source-constrained; no model output text, credentials, endpoint addresses or private deployment paths were persisted. This is a functional streaming check, not a latency or research-quality benchmark.
- The [initial harness receipt](harness-attempt.json) remains **inconclusive**: HTTP 200 and `readiness=timed_out` were observed, but the checker expected separate SSE event lines and did not decode this endpoint's JSON-data event types. Its assertion failure does not establish a product failure or a credited terminal success. The corrected parser follows the repository's compatibility runner. Delayed-probe and failed-generation behavior are directly covered by fixtures.

## Coverage boundary

The original CI changed-line summary reported 0/0 and was not used as coverage proof at delivery. The separate focused strict-mode run remains [34/35 changed executable lines](focused-coverage.md). Subsequent [artifact reprocessing](../coverage-binding/results.md) established that CI had measured all 35/35 changed executable lines; its relative `agent/...` report namespace was not bound correctly. Merged [PR #402](https://github.com/magnus919/groktocrawl-x/pull/402) fixes that binding and makes missing measurement explicit. Neither percentage is aggregate application coverage.

The separate [browser health follow-up](../browser-process-health/results.md) is also complete and deployed through PR #404. That update advances the agent revision while retaining this readiness behavior and model alias `free`. The earlier readiness deployment and live stream above remain historical evidence. No new replacement-readiness claim or mainline promotion is made by these repairs.
