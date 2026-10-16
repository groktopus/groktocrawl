from typing import Any

import httpx

from scripts.run_w11_provenance import digest, run_case


class FakeMcp:
    def __init__(self, *, eligible: bool) -> None:
        self.eligible = eligible
        self.receipt_id = "receipt-1"
        self.submits = 0

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "slopsearx_search":
            return {"results": [{"result_id": "snap:0"}]}
        if name == "slopsearx_read_result":
            return {
                "retrieval": {
                    "result_id": "snap:0",
                    "eligible": self.eligible,
                    "url": "https://example.com" if self.eligible else None,
                    "url_status": "ok" if self.eligible else "unsafe_scheme",
                }
            }
        if name == "slopsearx_submit_retrieval_receipt":
            self.submits += 1
            return {
                "state": "created" if self.submits == 1 else "replayed",
                "receipt": {
                    "receipt_id": self.receipt_id,
                    "result_id": "snap:0",
                    "discovery": {"result_id": "snap:0"},
                },
            }
        if name == "slopsearx_read_retrieval_receipts":
            return {"result_id": "snap:0", "observations_verified": False}
        if name == "slopsearx_export_research_manifest":
            return {
                "items": [{"result_id": "snap:0"}],
                "observations_verified": False,
                "verification_note": "not verified",
            }
        raise AssertionError(name)


def test_successful_capture_closes_receipt_manifest_chain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"markdown": "evidence", "metadata": {"sourceURL": "https://example.com"}},
            },
        )

    client = FakeMcp(eligible=True)
    with httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler)) as api:
        public, private = run_case(
            client, api, query="q", engines=["engine"], max_results=1, case_id="case"
        )
    assert public["hard_gate_passed"] is True
    assert public["items"][0]["content_sha256"] == digest("evidence")
    assert private["items"][0]["capture"]["markdown"] == "evidence"


def test_ineligible_handoff_is_not_fetched_and_is_receipted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected fetch: {request.url}")

    client = FakeMcp(eligible=False)
    with httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(handler)) as api:
        public, private = run_case(
            client, api, query="q", engines=["engine"], max_results=1, case_id="case"
        )
    assert public["hard_gate_passed"] is True
    assert public["items"][0]["capture_status"] == "failed"
    assert private["items"][0]["capture"]["failure_code"] == "handoff_ineligible"


def test_manifest_must_disclose_that_receipts_are_observations() -> None:
    class MisleadingManifest(FakeMcp):
        def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            value = super().call_tool(name, arguments)
            if name == "slopsearx_export_research_manifest":
                value.pop("verification_note")
            return value

    client = MisleadingManifest(eligible=False)
    with httpx.Client(
        base_url="https://api.test",
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError(request))),
    ) as api:
        public, _ = run_case(
            client, api, query="q", engines=["engine"], max_results=1, case_id="case"
        )
    assert public["hard_gate_passed"] is False
