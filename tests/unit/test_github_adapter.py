"""Regression tests for the GitHub adapter's fallback boundaries."""

from types import SimpleNamespace

import pytest
from scraper.adapters import github


class _RateLimitedClient:
    def __init__(self):
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers):
        self.urls.append(url)
        return SimpleNamespace(status_code=429, text="", content=b"")


@pytest.mark.asyncio
async def test_raw_readme_stops_after_rate_limit(monkeypatch):
    """A raw CDN rate limit must not trigger requests for other branches."""
    client = _RateLimitedClient()
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)

    result = await github._fetch_raw_readme("owner", "repo", ["main", "master"])

    assert result is None
    assert client.urls == [
        "https://raw.githubusercontent.com/owner/repo/main/README.md"
    ]
