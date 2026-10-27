# Runner preparation checkpoint

Candidate commit: `e75a884`. Reviewed runtime bundle SHA-256:
`cd8bec802a5d3e52a250d3a20b9f6670e6a7a17b3ba3e58477236390a1797c82`.
The manifest uses path/sha256 records; reconstruct admission runtime_files by path.
The runner is implemented, not executed live. No parent questions/labels received.

Completed: 69 focused runner/worker/budget/admission tests passed, Ruff passed,
and mypy passed for four runtime modules. Installed search offline probe confirms
one explicit Brave API request per search with no hidden specialist/suggestion
calls in the inspected frozen configuration. The model proxy offline probe confirms
explicit no-retry settings win key-style overrides, but global `drop_params=true`
can remove `max_tokens` on a second 422 repair request: observed caps `[8, null]`.
All provider responses were mocked and real socket connects denied. Zero real
provider/source/search calls occurred during these probes.

**Live admission is blocked.** `run_w12_live_screen.py` refuses this gateway before
reading a question packet. There is no valid finite requested output-token ceiling
on the currently configured repair path. Do not change production settings or route
around authentication. An owner-reviewed isolated route that cannot drop the cap is
needed before a new backend freeze can be admitted. A simple client timeout does
not cancel already dispatched work. No uncertain operation was repeated.

The three arms are `current_route` (upstream deep loop under the common restricted
profile), `fixed_diagnostic_control` (upstream focused loop), and
`obligation_bounded` (live W12.4-style named-fact continuation, existing admission
predicate, exact quote provenance, provisional materiality/closure, first no-gain
stop). Materiality remains a runtime proposal, not independent verification.
All use one explicit existing Brave engine, 2 results per query, common model/
sampler, 4k evidence context, and the same strict generic acquisition profile.
The main scraper's protected-source mode is off; the worker preserves its existing
public-destination guard and configured routing and adds mandatory fresh robots,
no redirects/adapters/cache/browser/recovery. This is explicitly a modified common
acquisition profile, not a claim about uncapped production performance. The lab
scraper has different source provenance and was inspected read-only, not used.

Conditional ceilings for an admissible no-cap-dropping backend: six attempts,
12 search RPCs, 24 acquisition RPCs, 24 model RPCs (60 logical operations); at most
12 Brave API HTTP calls, 48 source/robots GETs and 24 provider HTTP calls = 84
Internet application HTTP requests, or 120 including model/search service requests.
Token reservations: 6,144 requested output tokens, 288k serialized gateway input
bytes, 45 seconds per attempt and 300 seconds hard process deadline. These are
conditional proposed limits; they are NOT valid admission for today's gateway.
TCP/TLS/DNS/observability packets are outside these application-dispatch counts.
Actual total billing remains unknown, never zero by assumption.

Exports are exclusive local JSONL artifacts: `run-manifest.jsonl` (six planned
slots, bundle and budgets), `dispatches.jsonl` (fsynced intents before sending),
`backend-receipts.jsonl` (incremental child receipts), `attempts.jsonl` (exact answer,
source bodies/hashes/quote provenance and failures, grading parent-pending).
A watchdog exit or unresolved receipt prohibits replay; scheduled but undispatched
slots remain explicit. No grader or reference-answer implementation is included.

Independent local reviewer found and reproduced two P2s in the earlier adapter:
URL-alias exclusion bypass and cleanup outside the deadline. Both now have fixes
and regression tests. Final independent review confirms both further P2s (endpoint/proxy binding and robots scheme/port) resolved, with no additional P1/P2. Reviewed bundle matches above. No live dispatch is authorized.

Published head `5d8bdd3` runtime run `40525388378` reached terminal SUCCESS,
including Integration Tests, research-runtime Integration Tests and PostgreSQL
Storage Probes. No job was manually canceled/preempted or blindly rerun. CI for
this new candidate remains separate and must not be inferred from that green run.


## Scoped repair investigation and remaining approval

The installed provider retries 422 repair if global drop_params is true OR request
drop_params is true. Request false cannot override global true. The full installed
proxy route probe with explicit request false still dispatched caps [8, null]. A
detached-process-only global false probe dispatched one capped request for every
synthetic status, including 422. All HTTP was mocked with sockets denied: zero real
provider calls, no server configuration/route/credential changes. Existing request
retry controls cannot intercept this inner repair; post-response checks are too late.
No supported request-local repair switch has been established for this version.

Minimal additional authorization: an isolated temporary instance of the existing
gateway image/configuration with only drop_params=false and a runner-only endpoint
binding to it. Preserve the model, authentication and access controls; reuse existing
server-side credential mounts without exposing secret values, create no public
listener, and remove the instance after the bounded screen. This instance and scoped
route binding require explicit approval and have NOT been created. Production settings
remain unchanged. Once approved, freeze/review its exact configuration and repeat the
offline admission probes before receiving holdout questions. No budget increase or
admission waiver is proposed. Earlier unresolved operations remain excluded.

Head 4891c5a Code Quality found an unreachable test-generator yield and two false
positive secret findings on public code hashes. The generator now yields before
raising; the manifest stores path/sha256 records. Security checks and protected
fixtures are unchanged. Updated 69 focused tests, Ruff, and mypy on four runner
modules pass. New-head CI remains separately required.
