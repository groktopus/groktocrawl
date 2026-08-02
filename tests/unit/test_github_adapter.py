"""Regression tests for the GitHub adapter's fallback boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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


class _ReadmeVariantClient:
    def __init__(self):
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers):
        self.urls.append(url)
        if url.endswith("/README.rst"):
            return SimpleNamespace(
                status_code=200, text="README in reStructuredText", content=b"README"
            )
        return SimpleNamespace(status_code=404, text="", content=b"")


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


@pytest.mark.asyncio
async def test_raw_readme_finds_nonstandard_filename(monkeypatch):
    """The raw fallback should recover README variants matched by the API."""
    client = _ReadmeVariantClient()
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)

    result = await github._fetch_raw_readme("owner", "repo", ["main"])

    assert result == {
        "markdown": "README in reStructuredText",
        "source": "github-raw-readme",
        "metadata": {"file": "README.rst", "size": 6},
    }
    assert client.urls[-1].endswith("/main/README.rst")


@pytest.mark.asyncio
async def test_repo_root_falls_back_when_api_readme_is_empty(monkeypatch):
    """An empty API README must not suppress non-empty raw fallback content."""
    fetch_readme = AsyncMock(
        return_value={
            "markdown": "",
            "source": "github-readme-api",
            "metadata": {},
        }
    )
    fetch_metadata = AsyncMock(return_value={"default_branch": "main"})
    fetch_raw_readme = AsyncMock(
        return_value={
            "markdown": "raw README content",
            "source": "github-raw-readme",
            "metadata": {},
        }
    )
    monkeypatch.setattr(github, "_fetch_readme", fetch_readme)
    monkeypatch.setattr(github, "_fetch_repo_metadata", fetch_metadata)
    monkeypatch.setattr(github, "_fetch_raw_readme", fetch_raw_readme)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)

    result = await github.GitHubAdapter()._handle_repo_root(
        "https://github.com/owner/repo",
        {"owner": "owner", "repo": "repo"},
        None,
    )

    assert "raw README content" in result.markdown
    fetch_raw_readme.assert_awaited_once()
