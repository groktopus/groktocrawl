# Model readiness probe outcomes

For streaming `/v2/agent` cache misses, the candidate performs one minimal model
probe with a five-second wall-clock deadline. It does not retry that probe.
Ordinary `/health` reports service/dependency health and runtime identity; it is
not a guarantee that the next model generation will complete.

The `X-LLM-Readiness` response header identifies the preflight outcome:

| Outcome | Behavior |
|---|---|
| `ready` | Start normal streaming research. |
| `timed_out` | Readiness is indeterminate. Start one normal research stream under existing admission, generation timeouts and publication checks. Do not repeat the probe. |
| `unavailable` | Transport/connect failure or provider 5xx; return HTTP 503 before opening the stream. |
| `rate_limited` | Provider 429; return HTTP 503 with this classification before opening the stream. |
| `rejected` | Other non-200 response, such as authentication or model rejection; return HTTP 503 before opening the stream. |

A timed-out probe does not turn a failed generation into a successful result.
Actual generation errors retain their SSE error/terminal semantics. A cancelled
preflight closes its temporary client and never opens the stream. Cached replay
and non-streaming behavior remain unchanged; existing boolean health consumers
continue to treat only `ready` as healthy.

Readiness-probe logs record the bounded outcome, not provider bodies, credentials or endpoints.
When diagnosing a 503, distinguish `unavailable`, `rate_limited`, and `rejected`
from `timed_out`; never treat a five-second inference delay as proof that the
model is absent. The W9 first final attempt remains failed evidence; this fix
changes no historical result. Merged PR #396 was separately deployed to the
experimental agent and verified; see the [deployment and live-stream receipt](../experiments/model-readiness/results.md).
