"""Regression tests for the GitHub adapter's fallback boundaries."""

import asyncio
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
    def __init__(self, filename, branch=None):
        self.filename = filename
        self.branch = branch
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers):
        self.urls.append(url)
        if url.endswith(f"/{self.filename}") and (
            self.branch is None or f"/{self.branch}/" in url
        ):
            return SimpleNamespace(
                status_code=200, text="README variant content", content=b"README"
            )
        return SimpleNamespace(status_code=404, text="", content=b"")


class _DeadlineClient(_ReadmeVariantClient):
    async def get(self, url, headers):
        self.urls.append(url)
        await asyncio.sleep(0)
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

    result = await github._fetch_raw_readme(
        "owner", "repo", ["main", "master", "develop"]
    )

    assert result is None
    assert len(client.urls) == github.RAW_README_PROBE_LIMIT
    assert len(recorded) == github.RAW_README_PROBE_LIMIT
    assert client.urls[0].endswith("/main/README.md")
    assert client.urls[1].endswith("/master/README.md")
    assert client.urls[2].endswith("/develop/README.md")
    assert client.urls[3].endswith("/main/README.rst")
    assert client.urls[4].endswith("/master/README.rst")
    assert client.urls[5].endswith("/develop/README.rst")
    assert client.urls[6].endswith("/main/README.adoc")
    assert client.urls[7].endswith("/master/README.adoc")


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
@pytest.mark.parametrize("filename", ["README.rst", "README.adoc"])
async def test_raw_readme_finds_variant_on_later_branch(monkeypatch, filename):
    """Common variants on main/master remain inside the bounded product plan."""
    client = _ReadmeVariantClient(filename, branch="main")
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)

    result = await github._fetch_raw_readme(
        "owner", "repo", ["develop", "main", "master"]
    )

    assert result is not None
    assert result["metadata"]["file"] == filename
    assert any(f"/main/{filename}" in url for url in client.urls)


@pytest.mark.asyncio
async def test_raw_readme_stops_when_aggregate_deadline_expires(monkeypatch):
    """A stalled probe plan is cancelled without waiting for every request."""
    client = _DeadlineClient("never")
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)
    monkeypatch.setattr(github, "RAW_README_DEADLINE_SECONDS", 0)

    result = await github._fetch_raw_readme("owner", "repo", ["main"])

    assert result is None
    assert len(client.urls) <= 1


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


@pytest.mark.asyncio
async def test_repo_root_recovers_nonstandard_branch_after_api_readme_404(monkeypatch):
    """A default-branch 404 still probes standard branches for recovery."""
    fetch_readme = AsyncMock(return_value={"_not_found": True})
    fetch_metadata = AsyncMock(return_value={"default_branch": "develop"})
    fetch_raw_readme = AsyncMock(
        return_value={
            "markdown": "README from main",
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

    assert "README from main" in result.markdown
    fetch_raw_readme.assert_awaited_once_with(
        "owner", "repo", ["develop", "main", "master"]
    )
