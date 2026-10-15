"""Exception detail containment, not general secret detection or a sandbox."""

import hashlib
import json
import logging

import pytest
from agent.experimental.controller import ScriptResult

from tests.unit.test_knowledge_structure import payload  # noqa: F401
from tests.unit.test_scripted_controller import setup  # noqa: F401

CANARY = "SYNTHETIC_PRIVATE_canary_20260905"


@pytest.mark.parametrize("fault", ["runtime", "chained", "validation", "control"])
async def test_callback_canary_boundary(
    request, caplog, capsys, record_property, fault
):
    build = request.getfixturevalue("setup")
    _, _, publication = build()
    calls = []

    async def work():
        calls.append("work")
        if fault == "runtime":
            raise RuntimeError(CANARY)
        if fault == "chained":
            try:
                raise ValueError(CANARY)
            except ValueError as exc:
                raise RuntimeError("public wrapper") from exc
        if fault == "validation":
            # Pydantic's error contains the invalid value supplied by the callback.
            return ScriptResult.model_validate(
                {"output_id": "set1", "actual": {"tokens": CANARY}}
            )
        return ScriptResult(output_id="set1", actual={}, publication=publication)

    controller, _, _ = build(callbacks=[work])
    with caplog.at_level(logging.DEBUG):
        result = await controller.run()
        assert await controller.run() is result
    captured = capsys.readouterr()
    returned = result.model_dump_json()
    for surface in (returned, captured.out, captured.err, caplog.text):
        assert CANARY not in surface
    assert calls == ["work"]
    if fault == "control":
        assert result.execution_outcome == "completed"
        assert result.publication is not None
        assert "Price: $20" in returned
    else:
        assert result.execution_outcome == "failed"
        assert result.stop_reason == "operation_failed"
        assert result.publication is None
        assert len(result.accounting.operations) == 1
        assert result.accounting.operations[0].state == "pending"
    record_property(
        "canary_boundary_trace",
        json.dumps(
            {
                "variant": fault,
                "callback_calls": calls,
                "canary_sha256": hashlib.sha256(CANARY.encode()).hexdigest(),
                "outcome": result.execution_outcome,
                "reason": result.stop_reason,
                "returned_sha256": hashlib.sha256(returned.encode()).hexdigest(),
                "observed_surfaces": [
                    "returned_record",
                    "stdout",
                    "stderr",
                    "python_logs",
                ],
                "canary_found": False,
                "external_write_surface": "not_present",
            }
        ),
    )
