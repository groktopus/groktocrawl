"""Tests for agent-svc/agent/health.py — dependency health probes."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_check_valkey_ok():
    from agent.health import check_valkey

    mock_redis = MagicMock()
    with patch("redis.Redis.from_url", return_value=mock_redis):
        result = await check_valkey("redis://valkey:6379/0")

    assert result["status"] == "ok"
    assert "PING" in result["detail"]


@pytest.mark.asyncio
async def test_check_valkey_down():
    from agent.health import check_valkey

    mock_redis = MagicMock()
    mock_redis.ping.side_effect = ConnectionError("Connection refused")

    with patch("redis.Redis.from_url", return_value=mock_redis):
        result = await check_valkey("redis://valkey:6379/0")

    assert result["status"] == "down"


@pytest.mark.asyncio
async def test_check_searxng_ok():
    from agent.health import check_searxng

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "engines": [
            {"engine": "google", "results": 5},
            {"engine": "brave", "results": 3},
        ],
    }

    async def mock_get(url, params=None, headers=None):
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await check_searxng("http://searxng:8080")

        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_check_searxng_error():
    from agent.health import check_searxng

    async def mock_get(url, params=None, headers=None):
        import httpx

        raise httpx.ConnectError("Connection refused")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await check_searxng("http://searxng:8080")

        assert result["status"] in ("degraded", "down")


@pytest.mark.asyncio
async def test_check_scraper_ok():
    from agent.health import check_scraper

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def mock_get(url, **kw):
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await check_scraper("http://scraper-svc:8001")

        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_check_scraper_down():
    from agent.health import check_scraper

    async def mock_get(url, **kw):
        import httpx

        raise httpx.ConnectError("Connection refused")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await check_scraper("http://scraper-svc:8001")

        assert result["status"] == "down"


@pytest.mark.asyncio
async def test_check_browser_ok():
    from agent.health import check_browser

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    async def mock_get(url, **kw):
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await check_browser("http://browser-svc:8012")

        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_check_browser_down():
    from agent.health import check_browser

    async def mock_get(url, **kw):
        raise ConnectionError("No route")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await check_browser("http://browser-svc:8012")

        assert result["status"] == "down"


@pytest.mark.asyncio
async def test_check_all_overall_status():
    from agent.health import check_all

    with (
        patch(
            "agent.health.check_valkey",
            return_value={"status": "ok", "latency_ms": 2.0, "detail": "ok"},
        ),
        patch(
            "agent.health.check_searxng",
            return_value={"status": "ok", "latency_ms": 100.0, "detail": "ok"},
        ),
        patch(
            "agent.health.check_scraper",
            return_value={"status": "ok", "latency_ms": 5.0, "detail": "ok"},
        ),
        patch(
            "agent.health.check_browser",
            return_value={"status": "down", "latency_ms": 0.0, "detail": "down"},
        ),
    ):
        result = await check_all()
        assert result["status"] == "down"  # browser is down
        assert result["checks"]["valkey"]["status"] == "ok"
        assert result["checks"]["browser"]["status"] == "down"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary", "fallback", "expected"),
    [(200, None, "ok"), (503, None, "down")]
    + [
        (404, code, status)
        for code, status in [
            (200, "ok"),
            (302, "degraded"),
            (401, "degraded"),
            (403, "degraded"),
            (404, "degraded"),
            (429, "degraded"),
            (500, "down"),
            (503, "down"),
        ]
    ],
)
async def test_searxng_fallback_statuses(primary, fallback, expected):
    from agent.health import check_searxng

    responses = [httpx.Response(primary)]
    if fallback is not None:
        responses.append(httpx.Response(fallback))
    client = AsyncMock()
    client.get.side_effect = responses
    with patch("agent.health.httpx.AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = client
        result = await check_searxng("http://search:8080/")

    assert result["status"] == expected
    urls = [call.args[0] for call in client.get.await_args_list]
    assert urls == ["http://search:8080/health"] + (
        ["http://search:8080/config"] if fallback is not None else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_searxng_connection_failures(fallback, error):
    from agent.health import check_searxng

    client = AsyncMock()
    client.get.side_effect = ([httpx.Response(404)] if fallback else []) + [
        error("unavailable")
    ]
    with patch("agent.health.httpx.AsyncClient") as factory:
        factory.return_value.__aenter__.return_value = client
        result = await check_searxng("http://search:8080")
    assert result["status"] == "down"
