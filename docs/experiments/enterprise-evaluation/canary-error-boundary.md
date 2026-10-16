# Synthetic canaries in callback errors

The existing controller converts callback exceptions into `operation_failed`
without returning exception details. Four tests now exercise this actual boundary:
a runtime error containing a synthetic canary, a chained exception whose cause
contains it, a validation error with the canary as invalid input, and a successful
public-output control. No production code or filtering policy was changed.

The tests inspect the serialized returned result (including accounting), captured
stdout, stderr and Python logs. The marker is absent from all four surfaces in
these runs. Failure produces no publication; the public control still returns its
fixture evidence. Calling run again returns the same result without a second
callback. [Results](canary-fixture-results.json) record hashes and observed outcomes;
the tests use no real secrets or external tools.

This is partial evidence related to the secrets pair, not execution of its full
oracle. The adverse case does not ask a model to retrieve a secret or exercise an
injected instruction. There is no external write surface. A trusted callback can
still print, log or return arbitrary content, and successful source/publication
bodies are not scanned for secrets. Encodings, process memory, transport logs,
noncooperative late callbacks and third-party tools are outside this probe.

The eight previously counted scoped catalog cases are unchanged. These error-path
tests are tracked separately rather than inflating that count: the full secrets
and ambiguous-write scenarios remain planned, and full catalog execution is zero.

```sh
PYTHONPATH=agent-svc:. QA_OUTCOME_PATH=/tmp/canary-outcomes.json .venv/bin/python -m pytest tests/unit/test_controller_canary.py tests/unit/test_scripted_controller.py -o addopts='' -o junit_family=xunit1 --junitxml=/tmp/canary-traces.xml -q
```
