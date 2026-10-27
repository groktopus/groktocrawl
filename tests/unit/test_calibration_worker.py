"""Lower source dispatch counts use fake HTTP; no source requests."""

import asyncio

import httpx
import pytest

from scripts.w12_calibration_worker import BoundedSourceReads, acquire


@pytest.mark.asyncio
async def test_robots_redirect_is_not_followed_and_page_never_fetched():
    sent = []

    def respond(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://public.example/other"})

    reads = BoundedSourceReads(
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kwargs
        )
    )
    with pytest.raises(ValueError, match="redirect refused"):
        await reads.get("https://public.example/robots.txt", role="robots")
    with pytest.raises(RuntimeError, match="dispatch cap"):
        await reads.get("https://public.example/page", role="page")
    assert len(sent) == 1 and reads.halted


@pytest.mark.asyncio
async def test_unknown_robots_does_not_grant_page_permission(monkeypatch):
    from scraper import fetch, source_http

    monkeypatch.setattr(
        fetch, "_is_private_url", lambda url: asyncio.sleep(0, result=(False, ""))
    )
    sent = []

    def respond(request):
        sent.append(str(request.url))
        raise httpx.ReadTimeout("fixture timeout")

    monkeypatch.setattr(
        source_http,
        "source_httpx_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    with pytest.raises(httpx.ReadTimeout):
        await acquire(
            {"url": "https://public.example/page", "blocked_source_sha256": []}
        )
    assert sent == ["https://public.example/robots.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("robots_status", [301, 403, 500])
async def test_failed_robots_refuses_page_without_fallback(monkeypatch, robots_status):
    from scraper import fetch, source_http

    monkeypatch.setattr(
        fetch, "_is_private_url", lambda url: asyncio.sleep(0, result=(False, ""))
    )
    sent = []

    def respond(request):
        sent.append(str(request.url))
        return httpx.Response(robots_status, text="unavailable")

    monkeypatch.setattr(
        source_http,
        "source_httpx_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    result = await acquire(
        {"url": "https://public.example/page", "blocked_source_sha256": []}
    )
    assert result["success"] is False and len(sent) == 1


@pytest.mark.asyncio
async def test_robots_disallow_is_enforced_even_if_deployed_manager_is_disabled(
    monkeypatch,
):
    from scraper import fetch, source_http

    monkeypatch.setattr(
        fetch, "_is_private_url", lambda url: asyncio.sleep(0, result=(False, ""))
    )
    sent = []

    def respond(request):
        sent.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /page")

    monkeypatch.setattr(
        source_http,
        "source_httpx_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    result = await acquire(
        {"url": "https://public.example/page", "blocked_source_sha256": []}
    )
    assert result["success"] is False and result["error"] == "robots disallow"
    assert len(sent) == 1
