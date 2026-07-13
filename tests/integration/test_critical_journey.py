"""Diagnostic checks for the minimum fixture-backed product journey."""

import os

import httpx
import pytest

AGENT = os.getenv("AGENT_BASE_URL", "http://localhost:8080")
TEST_SITE = os.getenv("TEST_SITE_BASE_URL", "http://localhost:8005")


def _payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        pytest.fail(f"Expected JSON response: {response.text[:500]} ({exc})")
    assert isinstance(payload, dict), f"Expected JSON object: {response.text[:500]}"
    return payload


def test_stack_readiness():
    """stack readiness: the agent health endpoint reports an ok status."""
    response = httpx.get(AGENT + "/health", timeout=30)
    assert response.status_code == 200, (
        f"stack readiness returned {response.status_code}: {response.text[:500]}"
    )
    payload = _payload(response)
    assert payload.get("status") == "ok", f"stack readiness payload: {payload}"
    print("stack readiness: status=ok")


def test_search_contract():
    """search contract: fast search succeeds regardless of live result count."""
    response = httpx.post(
        AGENT + "/v2/search",
        json={"query": "fixture pricing", "search_type": "fast"},
        timeout=30,
    )
    assert response.status_code == 200, (
        f"search contract returned {response.status_code}: {response.text[:500]}"
    )
    payload = _payload(response)
    assert payload.get("success") is True, f"search contract payload: {payload}"
    data = payload.get("data")
    assert isinstance(data, dict), f"search contract payload: {payload}"
    web_results = data.get("web")
    assert isinstance(web_results, list), f"search contract payload: {payload}"
    print(f"search contract: result_count={len(web_results)}")


def test_fixture_scrape():
    """fixture scrape: the markdown-capable pricing fixture returns markdown."""
    response = httpx.post(
        AGENT + "/v2/scrape",
        json={"url": TEST_SITE + "/pricing"},
        timeout=30,
    )
    assert response.status_code == 200, (
        f"fixture scrape returned {response.status_code}: {response.text[:500]}"
    )
    payload = _payload(response)
    assert payload.get("success") is True, f"fixture scrape payload: {payload}"
    data = payload.get("data")
    assert isinstance(data, dict), f"fixture scrape payload: {payload}"
    markdown = data.get("markdown")
    assert isinstance(markdown, str), f"fixture scrape payload: {payload}"
    assert "Pro: $10" in markdown, f"fixture scrape markdown: {markdown[:500]}"
    print("fixture scrape: pricing markdown verified")
