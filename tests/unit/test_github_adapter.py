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
    def __init__(self, filename):
        self.filename = filename
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers):
        self.urls.append(url)
        if url.endswith(f"/{self.filename}"):
            return SimpleNamespace(
                status_code=200, text="README variant content", content=b"README"
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
async def test_raw_readme_reserves_shared_raw_budget(monkeypatch):
    """README probing must not consume the raw budget needed by other tiers."""
    client = _ReadmeVariantClient("never")
    recorded = []
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", recorded.append)

    result = await github._fetch_raw_readme("owner", "repo", ["main", "master"])

    assert result is None
    assert len(client.urls) == github.RAW_README_PROBE_LIMIT
    assert len(recorded) == github.RAW_README_PROBE_LIMIT
    assert client.urls[0].endswith("/main/README.md")
    assert client.urls[1].endswith("/master/README.md")
    assert all("/main/" in url for url in client.urls[2:])


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["README.rst", "README.adoc"])
async def test_raw_readme_finds_nonstandard_filename(monkeypatch, filename):
    """The raw fallback should recover API-matched README variants."""
    client = _ReadmeVariantClient(filename)
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)

    result = await github._fetch_raw_readme("owner", "repo", ["main"])

    assert result == {
        "markdown": "README variant content",
        "source": "github-raw-readme",
        "metadata": {"file": filename, "size": 6},
    }
    assert client.urls[-1].endswith(f"/main/{filename}")


@pytest.mark.asyncio
async def test_repo_root_falls_back_when_api_readme_decode_is_empty(monkeypatch):
    """A nonzero-size API README with empty decoded content can fall back."""
    fetch_readme = AsyncMock(
        return_value={
            "markdown": "",
            "source": "github-readme-api",
            "metadata": {"size": 1},
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


@pytest.mark.asyncio
async def test_repo_root_skips_raw_fallback_for_confirmed_empty_api_readme(monkeypatch):
    """A confirmed zero-byte API README should not burn raw CDN probes."""
    fetch_readme = AsyncMock(
        return_value={
            "markdown": "",
            "source": "github-readme-api",
            "metadata": {"size": 0},
        }
    )
    fetch_metadata = AsyncMock(return_value={"default_branch": "main"})
    fetch_raw_readme = AsyncMock()
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

    assert "## README" not in result.markdown
    fetch_raw_readme.assert_not_awaited()
