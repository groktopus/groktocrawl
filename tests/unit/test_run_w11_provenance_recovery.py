from typing import Any

import httpx
import pytest

from scripts.run_w11_provenance_recovery import InjectedInterruption, run_recovery_case


class FakeMcp:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.receipt: dict[str, Any] | None = None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls[name] = self.calls.get(name, 0) + 1
        if name == "slopsearx_search":
            return {"results": [{"result_id": "snap:0"}]}
        if name == "slopsearx_read_result":
            return {
                "retrieval": {
                    "result_id": "snap:0",
                    "eligible": True,
                    "url": "https://example.com",
                    "url_status": "ok",
                }
            }
        if name == "slopsearx_submit_retrieval_receipt":
            if self.receipt is None:
                self.receipt = {
                    "receipt_id": "receipt-1",
                    "result_id": "snap:0",
                    "discovery": {"result_id": "snap:0"},
                }
                return {"state": "created", "receipt": self.receipt}
            return {"state": "replayed", "receipt": self.receipt}
        if name == "slopsearx_read_retrieval_receipts":
            return {"result_id": "snap:0", "total": 1, "observations_verified": False}
        if name == "slopsearx_export_research_manifest":
            return {"items": [{"result_id": "snap:0"}], "observations_verified": False}
        raise AssertionError(name)


def successful_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {
                "markdown": "evidence",
                "metadata": {"sourceURL": "https://example.com"},
            },
        },
    )


def test_resume_after_interruption_during_capture_does_not_repeat_search() -> None:
    client = FakeMcp()
    checkpoint: dict[str, Any] = {}
    snapshots: list[dict[str, Any]] = []

    def save(value: dict[str, Any]) -> None:
        snapshots.append(dict(value))

    def interrupted_capture(_request: httpx.Request) -> httpx.Response:
        raise InjectedInterruption("during_downstream_retrieval")

    with httpx.Client(
        base_url="https://api.test", transport=httpx.MockTransport(interrupted_capture)
    ) as api:
        with pytest.raises(InjectedInterruption):
            run_recovery_case(
                client,
                api,
                query="q",
                engines=["engine"],
                case_id="capture",
                checkpoint=checkpoint,
                save=save,
            )

    with httpx.Client(
        base_url="https://api.test", transport=httpx.MockTransport(successful_response)
    ) as api:
        public, _ = run_recovery_case(
            client,
            api,
            query="q",
            engines=["engine"],
            case_id="capture",
            checkpoint=checkpoint,
            save=save,
        )

    assert public["hard_gate_passed"] is True
    assert public["audit"]["search_dispatches"] == 1
    assert public["audit"]["capture_attempts"] == 2
    assert client.calls["slopsearx_search"] == 1
    assert client.calls["slopsearx_read_result"] == 1


def test_resume_after_receipt_submission_replays_without_duplicate() -> None:
    client = FakeMcp()
    checkpoint: dict[str, Any] = {}

    def save(_value: dict[str, Any]) -> None:
        pass

    with httpx.Client(
        base_url="https://api.test", transport=httpx.MockTransport(successful_response)
    ) as api:
        with pytest.raises(InjectedInterruption):
            run_recovery_case(
                client,
                api,
                query="q",
                engines=["engine"],
                case_id="receipt",
                checkpoint=checkpoint,
                save=save,
                interrupt_after="receipt",
            )
        public, _ = run_recovery_case(
            client,
            api,
            query="q",
            engines=["engine"],
            case_id="receipt",
            checkpoint=checkpoint,
            save=save,
        )

    assert public["hard_gate_passed"] is True
    assert public["audit"] == {
        "search_dispatches": 1,
        "result_reads": 1,
        "capture_attempts": 1,
        "receipt_submissions": 2,
    }
    assert client.calls["slopsearx_submit_retrieval_receipt"] == 2
    assert public["retained_receipt_count"] == 1
