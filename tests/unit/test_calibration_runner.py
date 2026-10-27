"""Three-arm execution with synthetic provider/source fixtures, never live."""

import json

import pytest
from agent.experimental.calibration_budget import CalibrationLedger
from agent.experimental.calibration_runner import (
    ARMS,
    Clients,
    execute_screen,
    run_attempt,
    validate_questions,
)

HEADERS = {"x-litellm-attempted-retries": "0", "x-litellm-attempted-fallbacks": "0"}
INITIAL = "https://public.example/initial"
FOLLOWUP = "https://public.example/followup"
QUOTE = "The documented limit is exactly 42 widgets per day."


class Backend:
    def __init__(
        self,
        *,
        material=True,
        derivative=False,
        quote=QUOTE,
        duplicate=False,
        unknown=False,
    ):
        self.calls = []
        self.material, self.derivative, self.quote = material, derivative, quote
        self.duplicate, self.unknown = duplicate, unknown
        self.receipts = []

    async def call(self, kind, body, **kwargs):
        self.calls.append((kind, body))
        if self.unknown:
            raise TimeoutError("fixture worker lost")
        if kind == "search":
            url = FOLLOWUP if body.get("q") == "gap-query" else INITIAL
            return {
                "status_code": 200,
                "body": {
                    "results": [
                        {
                            "url": url,
                            "title": "Public fixture",
                            "content": "Source description",
                            "engine": "brave",
                        }
                    ],
                    "unresponsive_engines": [],
                },
            }
        if kind == "acquire":
            md = "Initial public documentation without the missing limit."
            if body["url"] == FOLLOWUP and not self.duplicate:
                md = QUOTE
            return {
                "success": True,
                "data": {"markdown": md, "source": "fixture"},
                "raw_sha256": "fixture-only",
                "events": [],
            }
        system = body["messages"][0]["content"]
        if "focused_queries" in system:
            answer = json.dumps(
                {"focused_queries": ["initial-query"], "research_strategy": "deep"}
            )
        elif "research gap analyzer" in system:
            answer = json.dumps({"topics": ["gap-query"]})
        elif "Identify at most one important" in system:
            answer = json.dumps(
                {
                    "missing_fact": "The exact limit",
                    "query": "gap-query",
                    "importance": 3,
                }
            )
        elif "Assess only this named" in system:
            answer = json.dumps(
                {
                    "support": [
                        {
                            "url": FOLLOWUP,
                            "quote": self.quote,
                            "material": self.material,
                            "derivative": self.derivative,
                        }
                    ]
                }
            )
        else:
            answer = "Source-bounded fixture answer. [1]"
        return {
            "status_code": 200,
            "body": {
                "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 20},
            },
            "headers": HEADERS,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ARMS)
async def test_real_three_arm_entrypoints_share_bounds_and_export_sources(arm):
    events = []
    book = CalibrationLedger("fixture-screen", events.append)
    backend = Backend()
    clients = Clients(book, backend, frozenset())
    await clients.open()
    book.start_attempt("fixture/" + arm)
    try:
        result = await run_attempt("Public documentation question", arm, clients)
        book.finish_attempt("completed")
    finally:
        await clients.close()
    assert result["result"] and result["source_contents"]
    assert sum(kind == "search" for kind, _ in backend.calls) <= 2
    assert sum(kind == "acquire" for kind, _ in backend.calls) <= 4
    assert sum(kind == "model" for kind, _ in backend.calls) <= 4
    if arm == "fixed_diagnostic_control":
        assert sum(kind == "search" for kind, _ in backend.calls) == 1
    elif arm == "obligation_bounded":
        assert result["obligation_trace"]["followups"] == 1
        assert result["obligation_trace"]["stop_reason"] == "provisional_closure"
        assert result["obligation_trace"]["closure_independently_verified"] is False
        assert FOLLOWUP in result["sources"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"material": False},
        {"derivative": True},
        {"quote": "Fabricated quote absent from the acquired source."},
        {"duplicate": True},
    ],
)
async def test_obligation_does_not_admit_unsupported_derivative_or_duplicate_evidence(
    options,
):
    book = CalibrationLedger("fixture-screen", lambda event: None)
    clients = Clients(book, Backend(**options), frozenset())
    await clients.open()
    book.start_attempt("fixture/obligation")
    try:
        result = await run_attempt("Public question", "obligation_bounded", clients)
        book.finish_attempt("completed")
    finally:
        await clients.close()
    assert result["obligation_trace"]["stop_reason"] == "no_material_gain"
    assert result["obligation_trace"]["followups"] == 1
    assert FOLLOWUP not in result["sources"]


def packet():
    return {
        "schema_version": "w12.4-first-screen-queries/1",
        "experiment_id": "fixture",
        "frozen_runner_sha256": "fixture-hash",
        "cases": [
            {"case_id": "fixture-a", "question": "Public question A"},
            {"case_id": "fixture-b", "question": "Public question B"},
        ],
    }


@pytest.mark.asyncio
async def test_six_scheduled_results_survive_a_global_unknown_stop():
    book = CalibrationLedger("fixture", lambda event: None)
    backend = Backend(unknown=True)
    results = []
    await execute_screen(packet(), book, backend, frozenset(), results.append)
    assert len(results) == 6
    assert len(backend.calls) == 1
    assert results[0]["status"] == "failed"
    assert all(x["status"] == "not_dispatched_after_global_stop" for x in results[1:])
    assert book.halted == "outcome_unknown"


@pytest.mark.asyncio
async def test_six_arm_screen_has_frozen_counterbalanced_order_and_no_grading():
    records = []
    book = CalibrationLedger("fixture", lambda event: None)
    await execute_screen(packet(), book, Backend(), frozenset(), records.append)
    assert len(records) == 6 and all(x["status"] == "completed" for x in records)
    assert [x["policy"] for x in records[:3]] == list(
        reversed([x["policy"] for x in records[3:]])
    )
    assert all(
        x["grading_status"] == "parent_pending" and x["total_cost_usd"] is None
        for x in records
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(labels=[]),
        lambda p: p["cases"][0].update(expected_answer="secret"),
        lambda p: p["cases"].append(p["cases"][0]),
        lambda p: p["cases"][1].update(case_id="fixture-a"),
    ],
)
def test_question_packet_rejects_labels_extra_cases_and_duplicates(change):
    value = packet()
    change(value)
    with pytest.raises(ValueError):
        validate_questions(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [True, False])
async def test_worker_receipts_are_retained_and_private_binding_is_not_exported(
    monkeypatch, unknown
):
    from pathlib import Path
    from types import SimpleNamespace

    from agent.experimental.calibration_budget import CalibrationStoppedError
    from agent.experimental.calibration_runner import SSHBackend

    packet = {}
    state = SimpleNamespace(returncode=None)

    class Writer:
        def write(self, data):
            packet.update(json.loads(data))

        async def drain(self):
            pass

        def close(self):
            pass

    class Reader:
        def __init__(self, stderr=False):
            self.sent, self.stderr = False, stderr

        async def read(self, limit):
            if self.sent or self.stderr:
                return b""
            self.sent = True
            receipt = {
                "operation_id": packet["operation_id"],
                "state": "outcome_unknown" if unknown else "terminal",
                "events": [{"state": "reserved"}],
                "result": {
                    "_private_user_binding": "private-binding",
                    "_private_key_fingerprint": "c" * 64,
                    "config_sha256": "a" * 64,
                    "policy_snapshot_sha256": "b" * 64,
                },
            }
            return b"W12_RECEIPT:" + json.dumps(receipt).encode() + b"\n"

    async def wait():
        state.returncode = 0

    state.stdin, state.stdout, state.stderr, state.wait = (
        Writer(),
        Reader(),
        Reader(True),
        wait,
    )

    async def create(*args, **kwargs):
        return state

    monkeypatch.setattr("asyncio.create_subprocess_exec", create)
    backend = SSHBackend(Path(__file__).resolve().parents[2])
    retained = []
    backend.receipt_sink = retained.append
    if unknown:
        with pytest.raises(CalibrationStoppedError, match="worker outcome unknown"):
            await backend.call("describe", {}, role="gateway")
        assert retained[0]["state"] == "outcome_unknown"
        assert "private-binding" not in json.dumps(retained)
        assert "c" * 64 not in json.dumps(retained)
    else:
        result = await backend.call("describe", {}, role="gateway")
        assert "private-binding" not in json.dumps(result)
        assert "private-binding" not in json.dumps(retained)
        assert backend._user_binding == "private-binding"
