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
