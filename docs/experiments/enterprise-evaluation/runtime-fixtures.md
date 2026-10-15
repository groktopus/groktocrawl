# Cancellation and pinned-source fixture checks

These four local probes exercise the existing `FixtureJourney` and its controller.
They do not run research comparisons, real tools, external writes or providers.
They require no human labels and do not establish research quality.

| Catalog case | Observed check |
|---|---|
| cancellation-adverse | Cancel during acquisition; even a callback that returns after receiving cancellation cannot publish or dispatch later stages. Re-running returns the same terminal result. |
| cancellation-control | Release the same acquisition normally; all four stages complete and publish once. |
| provenance-adverse | Append text outside the cited span under the planned snapshot identity; a pinned digest mismatch fails acquisition before construction or publication. |
| provenance-control | Matching normalized text completes; CRLF input normalizes to the pinned LF bytes. |

`SourceSpec.expected_digest` optionally pins SHA-256 of UTF-8 text after
`fixture-newlines/1` normalization (CRLF and CR become LF). It is included in the
plan's existing operation input digest. A mismatch fails the operation before the
snapshot is retained. Omitting the field preserves fresh acquisition behavior.
This verifies normalized text identity, not metadata authenticity, raw transport
bytes, prior ownership of an identifier, or source truth. Cross-revision identity
checks remain the separate history validator's responsibility.

The adversarial provenance test failed without the check: the changed source still
published because its quoted span was intact. The check makes that test pass while
the matching-source and existing unpinned acquisition tests continue to pass.

The tests attach callback/cancellation/terminal events and terminal ledger state to
JUnit properties. [Recorded local results](runtime-fixture-results.json) contain
four traces and hashes of the implementation and test file. Timings are monotonic
within each local run, not performance measurements. The trace is a scoped observer,
not the catalog's complete event protocol: it does not observe each internal stage
commit or external effect, and must not fill absent approval/effect data with
invented values. Therefore complete-catalog execution remains zero; six other
cases have no scoped implementation (see receipt coverage below). Missing required full-catalog events cannot
be reported as a full-catalog pass.

Reproduce from the repository root:

```sh
PYTHONPATH=agent-svc:. QA_OUTCOME_PATH=/tmp/runtime-outcomes.json .venv/bin/python -m pytest tests/unit/test_enterprise_runtime_cases.py tests/unit/test_fixture_pipeline.py -o addopts='' -o junit_family=xunit1 --junitxml=/tmp/runtime-traces.xml -q
```

Cancellation remains cooperative. This probe establishes rejection of a returned
late result, not the ability to stop a hostile process or undo an external effect.

## Receipt accounting: existing tests, scoped mapping

The idempotency pair reuses `test_duplicate_completion_is_idempotent_even_after_cancel`
in `tests/unit/test_execution_ledger.py`. Its first valid receipt spends three
tokens, releases the reservation and records one completion (control). Exact
replay while running and after cancellation returns the same state object; a
conflicting receipt is rejected without changing state (adverse). The test now
asserts these accounting invariants explicitly and records a JUnit property.

The existing controller stable-read tests assert that a repeated run returns the
same publication without another acquisition or publication callback. The
concurrent-run test rejects a second owner. These are supporting tests, not an
external receipt transport implementation. No duplicate fixtures or executor were
added, and production code is unchanged in this slice.

[Receipt results and input hashes](receipt-fixture-results.json) record the
40 passing ledger/controller tests and actual ledger snapshots. These snapshots
are not a full catalog event stream. No remote effects, restart, durable identity,
reconciliation or external exactly-once guarantee were exercised. Six catalog
cases now have scoped local evidence; complete catalog execution remains zero.

```sh
PYTHONPATH=agent-svc:. QA_OUTCOME_PATH=/tmp/receipt-outcomes.json .venv/bin/python -m pytest tests/unit/test_execution_ledger.py tests/unit/test_scripted_controller.py -o addopts='' -o junit_family=xunit1 --junitxml=/tmp/receipt-traces.xml -q
```
