"""Sanitized Food Lion regression: iframe challenges need no visible text."""

from contextlib import asynccontextmanager

import httpx
import pytest
from scraper.barrier import _classify_barrier

URL = "https://example.test/"
HTML = '<html><head><title>example.test</title></head><body><script src="https://ct.captcha-delivery.com/c.js"></script><iframe title="DataDome CAPTCHA" src="https://geo.captcha-delivery.com/captcha/?t=bv"></iframe></body></html>'


@pytest.mark.parametrize("mode", ["bv", "fe", "unknown"])
def test_datadome_iframe_is_definitive_without_text(mode):
    barrier = _classify_barrier(
        "example.test", URL, "", HTML.replace("t=bv", f"t={mode}")
    )
    assert barrier.provider == "datadome"
    assert barrier.barrier_type == "captcha"
    assert barrier.confidence > 0.7


@pytest.mark.parametrize(
    "html",
    [
        "<p>DataDome uses geo.captcha-delivery.com/captcha/.</p>",
        '&lt;iframe src="https://geo.captcha-delivery.com/captcha/"&gt;',
        '<iframe src="https://geo.captcha-delivery.com.evil.test/captcha/"></iframe>',
        '<iframe src="https://example.test/captcha/"></iframe>',
        '<iframe src="ftp://geo.captcha-delivery.com/captcha/"></iframe>',
        '<iframe src="javascript://geo.captcha-delivery.com/captcha/"></iframe>',
    ],
)
def test_mentions_and_lookalike_domains_are_not_datadome(html):
    assert _classify_barrier("Article", URL, "Content " * 100, html).provider is None


@pytest.mark.asyncio
async def test_datadome_resolver_reports_unsupported_without_clicks():
    from scraper.captcha import resolve_captcha

    class Page:
        url = URL

        async def content(self):
            return HTML

    barrier, attempts = await resolve_captcha(Page(), URL)
    assert barrier.provider == "datadome"
    assert attempts == ["unsupported_provider"]


def fake_client(monkeypatch, payload, status=200):
    import scraper.fetch_tiers as tiers

    class Client:
        async def post(self, *args, **kwargs):
            return httpx.Response(status, json=payload)

    @asynccontextmanager
    async def client(**kwargs):
        yield Client()

    monkeypatch.setattr(tiers, "trusted_control_httpx_client", client)
    return tiers


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403])
async def test_flare_classifies_short_datadome_before_status_and_length(
    monkeypatch, status
):
    tiers = fake_client(monkeypatch, {"solution": {"status": status, "response": HTML}})
    result = await tiers.fetch_via_flaresolverr(URL)
    assert result["barrier"]["provider"] == "datadome"
    assert result["barrier"]["supported"] is False
    assert result["http_status"] == status
    assert result["markdown"] == ""
    assert "captcha-delivery" not in str(result)


@pytest.mark.asyncio
async def test_flare_empty_is_not_service_unavailable(monkeypatch):
    tiers = fake_client(
        monkeypatch, {"solution": {"status": 200, "response": "<html></html>"}}
    )
    result = await tiers.fetch_via_flaresolverr(URL)
    assert result["failure_outcome"] == "no_content"


@pytest.mark.asyncio
async def test_recovery_does_not_retry_unsupported_provider(monkeypatch):
    import scraper.fetch as fetch

    async def forbidden(*args, **kwargs):
        pytest.fail("Unsupported DataDome must not use unrelated fallback services")

    monkeypatch.setattr(fetch, "fetch_via_flaresolverr", forbidden)
    monkeypatch.setattr(fetch, "_fetch_via_browser_svc", forbidden)
    result = await fetch._recover_from_barrier(
        URL,
        {
            "error": "Unsupported CAPTCHA provider: datadome",
            "error_code": "BARRIER_DETECTED",
            "http_status": 403,
            "barrier": {"type": "captcha", "provider": "datadome", "supported": False},
        },
        [],
    )
    assert result["http_status"] == 403
    assert result["recovery"]["provider"] == "datadome"
    assert result["recovery"]["attempts"][0]["outcome"] == "unsupported_challenge"
    assert all(a["outcome"] == "skipped" for a in result["recovery"]["attempts"][1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403])
async def test_browser_fallback_classifies_and_closes(monkeypatch, status):
    import scraper.fetch_tiers as tiers

    deleted = []

    class Client:
        async def post(self, path, json):
            if path.endswith("/browsers"):
                payload = {"id": "test"}
            elif json["action"] == "navigate":
                payload = {
                    "success": True,
                    "result": {"http_status": status, "title": "example.test"},
                }
            elif json["action"] == "getContent":
                payload = {"success": True, "result": {"html_length": len(HTML)}}
            else:
                payload = {"success": True, "result": {"script_result": HTML}}
            return httpx.Response(200, json=payload)

        async def delete(self, path):
            deleted.append(path)

    @asynccontextmanager
    async def client(**kwargs):
        yield Client()

    monkeypatch.setattr(tiers, "trusted_control_httpx_client", client)
    result = await tiers._fetch_via_browser_svc(URL)
    assert result["barrier"]["provider"] == "datadome"
    assert result["http_status"] == status
    assert len(deleted) == 1


@pytest.mark.asyncio
async def test_api_preserves_datadome_status(monkeypatch):
    import scraper.app as app
    from scraper.exceptions import BarrierDetectedError

    async def scrape(*args, **kwargs):
        return {
            "error": "Unsupported CAPTCHA provider: datadome",
            "error_code": "BARRIER_DETECTED",
            "http_status": 403,
            "barrier": {"provider": "datadome", "supported": False},
        }

    monkeypatch.setattr(app, "smart_scrape", scrape)
    with pytest.raises(BarrierDetectedError) as exc:
        await app.scrape(app.ScrapeRequest(url=URL))
    assert exc.value.details["http_status"] == 403
    assert exc.value.details["barrier"]["supported"] is False


@pytest.mark.asyncio
async def test_browser_failed_content_reads_are_not_empty_content(monkeypatch):
    import scraper.fetch_tiers as tiers

    deleted = []

    class Client:
        async def post(self, path, json):
            if path.endswith("/browsers"):
                payload = {"id": "test"}
            elif json["action"] == "navigate":
                payload = {"success": True, "result": {"http_status": 200}}
            elif json["action"] == "getContent":
                payload = {"success": True, "result": {"html_length": 100}}
            else:
                payload = {"success": False, "error": "fixture extraction failure"}
            return httpx.Response(200, json=payload)

        async def delete(self, path):
            deleted.append(path)

    @asynccontextmanager
    async def client(**kwargs):
        yield Client()

    monkeypatch.setattr(tiers, "trusted_control_httpx_client", client)
    result = await tiers._fetch_via_browser_svc(URL)
    assert result["failure_outcome"] == "extraction_failed"
    assert len(deleted) == 1
