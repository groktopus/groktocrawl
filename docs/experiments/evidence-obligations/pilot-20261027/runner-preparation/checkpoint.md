# Runner preparation checkpoint

Candidate commit: `0a0946c`. Runtime bundle SHA-256:
`145e1eccfb9a2f699b8bc080eb14c173bf34c112dfa9b2a29af83b8af4c3f69d`.
The runner is implemented, not executed live. No parent questions/labels received.

Completed: 66 focused runner/worker/budget/admission tests passed, Ruff passed,
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
and regression tests. Second scoped review of this full candidate is IN PROGRESS;
this checkpoint is not independent approval. No live dispatch is authorized.

Published head `5d8bdd3` runtime run `40525388378` reached terminal SUCCESS,
including Integration Tests, research-runtime Integration Tests and PostgreSQL
Storage Probes. No job was manually canceled/preempted or blindly rerun. CI for
this new candidate remains separate and must not be inferred from that green run.
