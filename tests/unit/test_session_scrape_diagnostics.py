"""Session failure receipts preserve refusal behavior without source/error prose."""

import json
from unittest.mock import AsyncMock

import pytest
from agent import session


@pytest.mark.parametrize(
    "result",
    [
        {
            "success": False,
            "error": {
                "code": "UPSTREAM_ERROR",
                "status_code": 502,
                "message": "private-error-prose",
            },
        },
        {
            "success": True,
            "warning": "private-warning",
            "data": {"markdown": "private-content"},
        },
        {"success": True, "data": {"markdown": ""}},
    ],
)
@pytest.mark.asyncio
async def test_unusable_scrape_is_diagnosed_without_creating_refs(
    monkeypatch, caplog, result
):
    class Scraper:
        def __init__(self, *_args, **_kwargs):
            pass

        async def scrape_with_fallback(self, *_args, **_kwargs):
            return result

        async def close(self):
            pass

    monkeypatch.setattr(session, "ScraperClient", Scraper)
    manager = session.SessionManager.__new__(session.SessionManager)
    manager._store_call = AsyncMock(return_value=1)
    manager._store_refs = AsyncMock()
    outcome = await manager._step_scrape(
        "session",
        {"urls": ["https://private.example/?token=private-query"]},
        "http://fixture",
    )
    assert outcome["refs"] == []
    assert outcome["succeeded"] == 0 and outcome["failed"] == 1
    records = [
        r for r in caplog.records if r.message.startswith("Session scrape yielded")
    ]
    assert len(records) == 1
    diagnostic = json.loads(records[0].args[0])
    assert len(diagnostic["source_sha256"]) == 64
    assert diagnostic["success"] is (result.get("success") is True)
    assert diagnostic["warning_present"] is bool(result.get("warning"))
    assert "private" not in caplog.text


def test_unknown_error_and_malformed_quality_cannot_become_log_prose():
    result = {
        "success": False,
        "error_code": "PRIVATE_SECRET_VALUE",
        "error": "private-error",
        "status_code": True,
        "data": {"quality": {"checks": {"block_detected": ["private-block"]}}},
    }
    diagnostic = session._session_scrape_diagnostic("https://private.example", result)
    assert diagnostic["error_code"] == "other"
    assert diagnostic["block_detected"] == "unknown"
    assert diagnostic["status_code"] is None
    assert "private" not in json.dumps(diagnostic)
    assert "SECRET" not in json.dumps(diagnostic)


def test_exception_is_classified_without_exception_message():
    diagnostic = session._session_scrape_diagnostic(
        "https://private.example", exception=TimeoutError("private-error")
    )
    assert (
        diagnostic["stage"] == "exception" and diagnostic["exception_kind"] == "timeout"
    )
    assert "private" not in json.dumps(diagnostic)
