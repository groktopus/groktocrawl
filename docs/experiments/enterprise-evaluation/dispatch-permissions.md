# Local owner-supplied dispatch permissions

The existing `ScriptedController` accepts optional `authorized_operations`:
a bounded tuple of `OperationSpec` records supplied by the trusted local owner.
Before each reservation or callback dispatch, the controller checks exact equality
of operation ID, input digest, output ID and reserved budget against that list.
A missing or altered spec fails with `operation_not_authorized`; an empty tuple
denies all operations. Duplicate permission identities are rejected at setup.

Omitting the argument preserves the existing trusted-script fixtures. There is no
implicit production permission grant here: this controller is an in-process
prototype with trusted callbacks, not a process sandbox or authenticated API.
The permission list is copied and revalidated at setup; retrieved source content
cannot supply it. A callback's Python implementation is not authenticated or bound
by the input digest. Malicious in-process code can bypass this boundary. Future
real tools still require explicit identity, scoped approvals and effect controls.

The catalog authorization control now has a scoped local check: matching owner
permissions allow acquisition and publication. Its adverse check has five variants:
empty permissions or altered input digest, output identity, budget or operation
identity. They dispatch no callback and reserve/spend nothing. A further test
permits acquisition but not publication, proving the check runs at each dispatch.
These callbacks are local fixture operations, not real deployment writes.

`test_owner_permissions_gate_dispatch` emits JUnit trace properties with callback
calls, terminal reason and ledger state. [Recorded results](dispatch-fixture-results.json)
pin implementation/test hashes. These are scoped observations, not the complete
catalog event protocol or a real deployment authorization test. Eight catalog cases
now have scoped local evidence; complete catalog execution remains zero. Secrets
and ambiguous-write scenarios still have no scoped implementation.

```sh
PYTHONPATH=agent-svc:. QA_OUTCOME_PATH=/tmp/grants-outcomes.json .venv/bin/python -m pytest tests/unit/test_scripted_controller.py tests/unit/test_execution_ledger.py tests/unit/test_fixture_pipeline.py -o addopts='' -o junit_family=xunit1 --junitxml=/tmp/grants-traces.xml -q
```
