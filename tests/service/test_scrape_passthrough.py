"""Agent-layer pass-through tests for the scrape extraction diagnostic (#587).

The public ``POST /v2/scrape`` surface must expose what scraper-svc reports:
- ``data.quality`` populated from the scraper's quality assessment, and
- a top-level ``warning`` field mirroring the scraper's low-yield warning.

The route adds no truncation beyond what scraper-svc delivered (the markdown
field is passed through verbatim).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from agent.models import ScrapeData, ScrapeResponse
from agent.routes import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCRAPER_PAYLOAD_FULL = {
    "success": True,
    "data": {
        "markdown": "x" * 11000,
        "metadata": {"title": "Hardware Directory"},
        "quality": {
            "score": 0.9,
            "checks": {
                "boilerplate": "pass",
                "completeness": "pass",
                "block_detected": "pass",
                "volume": "pass",
            },
            "detail": "all checks passed",
        },
    },
}

SCRAPER_PAYLOAD_LOW_YIELD = {
    "success": True,
    "data": {
        "markdown": "# Intro\n\nShort intro paragraph only.",
        "metadata": {"title": "Hardware Directory"},
        "quality": {
            "score": 0.35,
            "checks": {
                "boilerplate": "pass",
                "completeness": "warn",
                "block_detected": "pass",
                "volume": "fail",
            },
            "detail": "volume:fail",
        },
    },
    "warning": (
        "Low yield: extracted 38 chars from a 94978-char HTML source "
        "(ratio 0.0004, floor 0.02). The page body may be truncated."
    ),
}

SCRAPER_PAYLOAD_RECOVERED = {
    "success": True,
    "data": {
        "markdown": "Recovered article content",
        "metadata": {"title": "Recovered"},
        "recovery": {
            "trigger": "barrier",
            "barrier_type": "captcha",
            "provider": "recaptcha",
            "attempts": [
                {"strategy": "playwright", "outcome": "barrier"},
                {"strategy": "browser-svc", "outcome": "success"},
            ],
            "outcome": "recovered",
            "exhausted": False,
        },
    },
}


def _build_app(scraper_payload: dict) -> FastAPI:
    """Minimal harness mirroring create_app wiring for the scrape route."""
    app = FastAPI()
    app.state.rate_limiter = MagicMock()
    app.state.job_store = MagicMock()
    app.state.max_searches_per_request = 5
    app.state.task_tracker = MagicMock()

    scraper_client = MagicMock()
    scraper_client.scrape = AsyncMock(return_value=scraper_payload)
    scraper_client.close = AsyncMock()
    app.state.scraper_client = scraper_client

    app.add_exception_handler = app.add_exception_handler  # keep harness explicit
    app.include_router(router)
    return app


# ── Model contracts ──────────────────────────────────────────────


def test_scrape_response_has_warning_field():
    """ScrapeResponse carries an optional warning field (None default)."""
    assert "warning" in ScrapeResponse.model_fields
    assert ScrapeResponse(success=True).warning is None


def test_scrape_data_quality_default_none():
    """ScrapeData keeps quality as an optional dict (existing contract)."""
    assert "quality" in ScrapeData.model_fields
    assert ScrapeData().quality is None


def test_scrape_data_recovery_default_none():
    assert "recovery" in ScrapeData.model_fields
    assert ScrapeData().recovery is None


# ── Route pass-through ───────────────────────────────────────────


def test_v2_scrape_passes_through_warning_and_quality():
    """/v2/scrape mirrors scraper-svc warning + quality on low-yield results.

    The #586 barrier refusal only fires for challenge/interstitial flags
    (block_detected warn/fail); the #587 low-yield warning keeps its
    pass-through contract — visible on the surface, still a success.
    """
    client = TestClient(_build_app(SCRAPER_PAYLOAD_LOW_YIELD))
    resp = client.post(
        "/v2/scrape", json={"url": "https://example.test/hw", "formats": ["markdown"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["warning"], "low-yield warning must reach the public surface"
    assert body["data"]["quality"]["checks"]["volume"] == "fail"


def test_v2_scrape_no_warning_on_healthy_payload():
    """/v2/scrape stays silent when the scraper reported no warning."""
    client = TestClient(_build_app(SCRAPER_PAYLOAD_FULL))
    resp = client.post(
        "/v2/scrape", json={"url": "https://example.test/hw", "formats": ["markdown"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not body.get("warning")
    assert len(body["data"]["markdown"]) == 11000  # no truncation added


def test_v2_scrape_passes_markdown_verbatim():
    """Agent layer applies no size cap or re-extraction to markdown."""
    payload = SCRAPER_PAYLOAD_FULL
    client = TestClient(_build_app(payload))
    resp = client.post(
        "/v2/scrape", json={"url": "https://example.test/hw", "formats": ["markdown"]}
    )
    body = resp.json()
    assert body["data"]["markdown"] == payload["data"]["markdown"]


def test_v2_scrape_passes_through_recovery_receipt():
    client = TestClient(_build_app(SCRAPER_PAYLOAD_RECOVERED))
    resp = client.post(
        "/v2/scrape", json={"url": "https://example.test/hw", "formats": ["markdown"]}
    )
    assert resp.status_code == 200
    recovery = resp.json()["data"]["recovery"]
    assert recovery["outcome"] == "recovered"
    assert recovery["attempts"][-1] == {
        "strategy": "browser-svc",
        "outcome": "success",
    }
