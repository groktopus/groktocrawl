"""External GitHub compatibility signal, separate from deterministic gates.

Deterministic adapter-contract tests never call GitHub. This probe calls the
deployed scraper service and therefore runs only when external connectivity is
intended. Its assertions are deliberately semantic and small; diagnostics keep
the observed status, response body, and exception available to distinguish
service/adapter errors, authentication or rate limits, upstream failures,
malformed responses, and source-content changes.
"""

import json
import os

import httpx
import pytest

SCRAPER = os.getenv("SCRAPER_BASE_URL", "http://localhost:8001")
GITHUB_REPO = "https://github.com/groktopus/groktocrawl"
GITHUB_ISSUE = "https://github.com/groktopus/groktocrawl/issues/1"


def _failure_category(
    status: int | None, body: str, error: Exception | None = None
) -> str:
    if error is not None:
        return "upstream network failure"
    if status in {401, 403, 429}:
        return "authentication or GitHub rate limiting"
    if status is not None and status >= 500:
        return "upstream or scraper service 5xx failure"
    if status is not None and status >= 400:
        return "adapter or scraper service failure"
    if not body:
        return "malformed empty service response"
    return "expected source-content change"


def _scrape_github_probe(url: str, label: str) -> tuple[str, str, int, str]:
    """Fetch one GitHub URL and retain bounded failure diagnostics."""
    try:
        response = httpx.post(SCRAPER + "/scrape", json={"url": url}, timeout=15)
    except Exception as exc:
        pytest.fail(f"{label}: {_failure_category(None, '', exc)}: {exc}")

    body = response.text
    if response.status_code != 200:
        pytest.fail(
            f"{label}: {_failure_category(response.status_code, body)}; "
            f"status={response.status_code}; body={body[:1000]!r}"
        )

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        pytest.fail(
            f"{label}: malformed non-JSON service response; "
            f"status={response.status_code}; body={body[:1000]!r}; error={exc}"
        )

    if not isinstance(payload, dict) or not payload.get("success"):
        pytest.fail(
            f"{label}: adapter or scraper service failure; "
            f"status={response.status_code}; body={body[:1000]!r}"
        )

    data = payload.get("data")
    markdown = data.get("markdown") if isinstance(data, dict) else None
    source = data.get("source") if isinstance(data, dict) else None
    if (
        not isinstance(markdown, str)
        or not markdown.strip()
        or not isinstance(source, str)
    ):
        pytest.fail(
            f"{label}: malformed service response; "
            f"status={response.status_code}; body={body[:1000]!r}"
        )

    return markdown, source, response.status_code, body


def test_github_probe_rejects_malformed_success_payload(monkeypatch):
    """A successful response still requires the scraper payload contract."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(
            200, json={"success": True, "data": {"markdown": "content"}}
        ),
    )

    with pytest.raises(pytest.fail.Exception, match="malformed service response"):
        _scrape_github_probe("https://github.com/fixture/repo", "GitHub probe")


@pytest.mark.external
def test_github_adapter_live_compatibility_probe():
    """The deployed scraper still recognizes a public GitHub repo root."""
    markdown, source, status, body = _scrape_github_probe(
        GITHUB_REPO, "GitHub compatibility probe"
    )
    if not source.startswith("github"):
        pytest.fail(
            f"GitHub compatibility probe: adapter or scraper service failure; "
            f"status={status}; source={source!r}; body={body[:1000]!r}"
        )
    assert "GroktoCrawl" in markdown, (
        "GitHub compatibility probe: expected source-content change; "
        f"status={status}; source={source!r}; body={body[:1000]!r}"
    )


@pytest.mark.external
def test_github_adapter_live_issue_probe():
    """The deployed scraper still recognizes a public GitHub issue."""
    markdown, source, _status, _body = _scrape_github_probe(
        GITHUB_ISSUE, "GitHub issue compatibility probe"
    )
    assert source == "github-social-adapter", (
        "GitHub issue compatibility probe: expected GitHub social source; "
        f"source={source!r}"
    )
    assert "## Description" in markdown, (
        "GitHub issue compatibility probe: expected issue content marker; "
        f"source={source!r}"
    )
