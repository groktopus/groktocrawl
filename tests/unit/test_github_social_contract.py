"""Deterministic contract coverage for the GitHub social issue adapter."""

import json
from pathlib import Path

import httpx
import pytest
from scraper.adapters import github_social
from scraper.adapters.base import AdapterContext

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
ISSUE_URL = "https://github.com/fixture/repo/issues/42"


def _fixture_json(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return async_client(transport=transport, **kwargs)

    monkeypatch.setattr(github_social.httpx, "AsyncClient", client_factory)


@pytest.mark.asyncio
async def test_issue_contract_uses_rest_issue_and_comments_endpoints(monkeypatch):
    issue = _fixture_json("issue.json")
    comments = _fixture_json("issue-comments.json")
    requests = []

    async def no_graphql(*args, **kwargs):
        return None

    monkeypatch.setattr(github_social, "_graphql", no_graphql)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/fixture/repo/issues/42":
            assert list(request.url.params.multi_items()) == []
            return httpx.Response(200, request=request, json=issue)
        if request.url.path == "/repos/fixture/repo/issues/42/comments":
            assert list(request.url.params.multi_items()) == [("per_page", "100")]
            return httpx.Response(200, request=request, json=comments)
        raise AssertionError(f"Unexpected GitHub REST path: {request.url}")

    _install_transport(monkeypatch, handler)
    result = await github_social.GitHubSocialAdapter().scrape(
        ISSUE_URL, AdapterContext()
    )

    assert [request.url.path for request in requests] == [
        "/repos/fixture/repo/issues/42",
        "/repos/fixture/repo/issues/42/comments",
    ]
    assert result.success is True
    assert result.source == "github-social-adapter"
    assert result.url == ISSUE_URL
    assert result.metadata == {
        "source": "github-social-adapter",
        "resource": "issue",
        "title": "Improve fixture-backed issue coverage",
        "state": "open",
        "author": "fixture-author",
        "created": "2026-01-15",
        "comment_count": 2,
        "labels": ["testing", "documentation"],
    }
    assert "# Improve fixture-backed issue coverage" in result.markdown
    assert "## Description" in result.markdown
    assert (
        "This issue verifies the deterministic REST adapter contract."
        in result.markdown
    )
    assert "## Comments  (2)" in result.markdown
    assert "### @fixture-reviewer  _(2026-01-16)_" in result.markdown
    assert "The REST response should preserve this comment." in result.markdown
    assert "### @fixture-maintainer  _(2026-01-17)_" in result.markdown
