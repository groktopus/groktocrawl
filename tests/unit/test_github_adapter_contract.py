"""Deterministic contract tests for the real GitHub adapter path."""

import base64
import json
from pathlib import Path

import httpx
import pytest
from scraper.adapters import github, github_social
from scraper.adapters.base import (
    AdapterContext,
    AdapterError,
    AdapterRegistry,
    AdapterResult,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
RAW_URL = "https://raw.githubusercontent.com/fixture/repo/main/raw-file.txt"
BLOB_URL = "https://github.com/fixture/repo/blob/main/README.md"
BLOB_RAW_URL = "https://raw.githubusercontent.com/fixture/repo/main/README.md"
REPO_URL = "https://github.com/fixture/repo"
TREE_URL = "https://github.com/fixture/repo/tree/main/src"


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _fixture_json(name: str) -> dict | list:
    return json.loads(_fixture_text(name))


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(github.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(github._rate_tracker, "can_call", lambda endpoint: True)
    monkeypatch.setattr(github._rate_tracker, "record_call", lambda endpoint: None)


def _json_response(request: httpx.Request, payload: dict | list) -> httpx.Response:
    return httpx.Response(200, request=request, json=payload)


@pytest.mark.asyncio
async def test_raw_url_contract_uses_real_adapter(monkeypatch):
    content = _fixture_text("raw-file.txt")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == RAW_URL
        return httpx.Response(200, request=request, text=content)

    _install_transport(monkeypatch, handler)
    result = await github.GitHubAdapter().scrape(RAW_URL, AdapterContext())

    assert result.success is True
    assert result.source == "raw.githubusercontent.com"
    assert result.url == RAW_URL
    assert result.metadata == {
        "source": "github-adapter",
        "url_type": "raw",
        "owner": "fixture",
        "repo": "repo",
        "ref": "main",
        "path": "raw-file.txt",
        "size": len(content),
        "encoding": "utf-8",
    }
    assert result.markdown == f"```\n{content}\n```"
    assert result.to_dict()["markdown"].startswith("---\nsource: github-adapter\n")


@pytest.mark.asyncio
async def test_blob_url_contract_rewrites_to_controlled_raw_source(monkeypatch):
    content = _fixture_text("readme.md")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == BLOB_RAW_URL
        return httpx.Response(200, request=request, text=content)

    _install_transport(monkeypatch, handler)
    result = await github.GitHubAdapter().scrape(BLOB_URL, AdapterContext())

    assert result.success is True
    assert result.source == "raw.githubusercontent.com"
    assert result.url == BLOB_URL
    assert result.metadata["url_type"] == "blob"
    assert result.metadata["path"] == "README.md"
    assert "Fixture Repository" in result.markdown


@pytest.mark.asyncio
async def test_tree_url_contract_formats_directory_metadata(monkeypatch):
    tree = _fixture_json("tree.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/fixture/repo/contents/src"
        assert request.url.params["ref"] == "main"
        return _json_response(request, tree)

    _install_transport(monkeypatch, handler)
    result = await github.GitHubAdapter().scrape(TREE_URL, AdapterContext())

    assert result.success is True
    assert result.source == "github-contents-api"
    assert result.metadata["url_type"] == "tree"
    assert result.metadata["item_count"] == 2
    assert result.markdown == (
        "# src\n\n*2 items*\n\n📁 **adapters/**\n📄 README.md  _(123 bytes)_\n"
    )


@pytest.mark.asyncio
async def test_repo_root_contract_combines_readme_and_repository_metadata(monkeypatch):
    readme = _fixture_text("readme.md")
    repository = _fixture_json("repository.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/readme"):
            return _json_response(
                request,
                {
                    "name": "README.md",
                    "size": len(readme),
                    "encoding": "base64",
                    "content": base64.b64encode(readme.encode()).decode(),
                },
            )
        assert request.url.path == "/repos/fixture/repo"
        return _json_response(request, repository)

    _install_transport(monkeypatch, handler)
    result = await github.GitHubAdapter().scrape(REPO_URL, AdapterContext())

    assert result.success is True
    assert result.source == "github-adapter"
    assert result.url == REPO_URL
    assert result.metadata == {
        "source": "github-adapter",
        "url_type": "repo_root",
        "owner": "fixture",
        "repo": "repo",
        "size": len(readme),
        "description": "A controlled GitHub adapter fixture.",
        "stars": 17,
        "forks": 3,
        "language": "Python",
    }
    assert (
        result.markdown
        == (
            "# fixture/repo\n\n> A controlled GitHub adapter fixture.\n\n"
            "⭐ 17 stars | 🍴 3 forks | 🔤 Python | 📜 MIT | 🌿 main\n\n"
            "🏷️  `fixture`, `scraping`\n\n---\n## README\n\n"
            f"{readme}"
        ).strip()
    )
    output = result.to_dict()
    assert output["source"] == "github-adapter"
    assert "url_type: repo_root" in output["markdown"]
    assert "## README" in output["markdown"]


@pytest.mark.asyncio
async def test_registry_dispatch_contract_covers_github_priority_and_fallthrough(
    monkeypatch,
):
    registry = AdapterRegistry()
    github_adapter = github.GitHubAdapter()
    social_adapter = github_social.GitHubSocialAdapter()
    calls = []

    async def github_scrape(url, _ctx):
        calls.append(("github", url))
        if "/issues/" in url or "/pull/" in url:
            raise AdapterError("social URL")
        return AdapterResult(
            success=True,
            markdown="github result",
            source="github",
            url=url,
        )

    async def social_scrape(url, _ctx):
        calls.append(("github-social", url))
        return AdapterResult(
            success=True,
            markdown="social result",
            source="github-social",
            url=url,
        )

    monkeypatch.setattr(github_adapter, "scrape", github_scrape)
    monkeypatch.setattr(social_adapter, "scrape", social_scrape)
    registry.register(social_adapter)
    registry.register(github_adapter)

    assert [entry.name for entry in registry._entries] == ["github", "github-social"]

    urls = [
        RAW_URL,
        BLOB_URL,
        TREE_URL,
        REPO_URL,
        "https://github.com/fixture/repo/issues/42",
        "https://github.com/fixture/repo/pull/7",
    ]
    for url in urls:
        result = await registry.dispatch(url, AdapterContext())
        assert result is not None
        assert result.source == (
            "github-social" if "/issues/" in url or "/pull/" in url else "github"
        )
        assert result.url == url

    assert calls == [
        ("github", RAW_URL),
        ("github", BLOB_URL),
        ("github", TREE_URL),
        ("github", REPO_URL),
        ("github", "https://github.com/fixture/repo/issues/42"),
        ("github-social", "https://github.com/fixture/repo/issues/42"),
        ("github", "https://github.com/fixture/repo/pull/7"),
        ("github-social", "https://github.com/fixture/repo/pull/7"),
    ]
