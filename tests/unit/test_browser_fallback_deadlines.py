"""The caller cannot time out a still-within-budget navigation."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scraper-svc"))


@pytest.mark.asyncio
async def test_browser_fallback_budget_includes_navigation_and_cleanup(monkeypatch):
    from scraper import fetch_tiers

    timeouts = []
    navigation = []
    deleted = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, json):
            if url.endswith("/browsers"):
                return SimpleNamespace(status_code=200, json=lambda: {"id": "test"})
            navigation.append(json["timeout"] / 1000)
            return SimpleNamespace(json=lambda: {"success": False})

        async def delete(self, url):
            deleted.append(url)

    def client(**kwargs):
        timeouts.append(kwargs["timeout"])
        return Client()

    monkeypatch.setattr(fetch_tiers, "trusted_control_httpx_client", client)
    await fetch_tiers._fetch_via_browser_svc("https://example.test")
    assert timeouts[0] >= navigation[0] + 10
    assert timeouts[1] >= 4 * 2 + 10
    assert len(deleted) == 1


@pytest.mark.asyncio
async def test_browser_fallback_rejects_http_error_navigation(monkeypatch):
    from scraper import fetch_tiers

    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, json):
            calls.append(json.get("action", "create"))
            if url.endswith("/browsers"):
                return Response({"id": "session"})
            return Response(
                {
                    "success": True,
                    "result": {
                        "http_status": 404,
                        "title": "Not Found",
                        "url": "https://example.test/missing",
                    },
                }
            )

        async def delete(self, _url):
            calls.append("delete")

    monkeypatch.setattr(
        fetch_tiers,
        "trusted_control_base_url",
        lambda *_args: "http://browser.test",
    )
    monkeypatch.setattr(
        fetch_tiers,
        "trusted_control_httpx_client",
        lambda **_kwargs: Client(),
    )

    result = await fetch_tiers._fetch_via_browser_svc("https://example.test/missing")

    assert result["error_code"] == "HTTP_STATUS_ERROR"
    assert result["http_status"] == 404
    assert result["markdown"] == ""
    assert calls == ["create", "navigate", "delete"]


@pytest.mark.asyncio
async def test_http_error_navigation_is_terminal_in_smart_scrape(monkeypatch):
    from scraper import fetch

    async def no_destination_error(*_args, **_kwargs):
        return None

    async def allowed(*_args, **_kwargs):
        return True, None

    async def http_error(_url):
        return {
            "error": "Source navigation returned HTTP 404",
            "error_code": "HTTP_STATUS_ERROR",
            "http_status": 404,
            "markdown": "",
            "source": "playwright",
        }

    async def unchanged(result, _url):
        return result

    monkeypatch.setattr(fetch, "_private_destination_error", no_destination_error)
    monkeypatch.setattr(fetch, "_politeness_check_and_delay", allowed)
    monkeypatch.setattr(fetch, "fetch_via_playwright", http_error)
    monkeypatch.setattr(fetch, "_enrich_with_politeness", unchanged)
    monkeypatch.setattr(
        fetch,
        "_maybe_degrade",
        lambda *_args, **_kwargs: pytest.fail("HTTP errors must not be quality-graded"),
    )

    result = await fetch.smart_scrape(
        "https://example.test/missing", force_browser=True
    )

    assert result["error_code"] == "HTTP_STATUS_ERROR"
    assert result["http_status"] == 404
    assert result["markdown"] == ""
