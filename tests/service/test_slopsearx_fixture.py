"""Real-HTTP contract tests for the deterministic SlopSearX fixture."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn

sys.path.insert(0, str(Path(__file__).parents[2] / "slopsearx-fixture"))

from slopsearx_fixture.app import (
    MAX_LEDGER,
    SCHEMA_VERSION,
    FixtureState,
    SearchResponse,
    create_app,
)


def test_scenario_request_partitions_are_hard_bounded():
    state = FixtureState()
    for index in range(MAX_LEDGER + 5):
        state.next_scenario_request("healthy", f"query-{index}", "run-a")
    assert len(state.scenario_requests) == MAX_LEDGER
    newest_key = next(reversed(state.scenario_requests))
    assert newest_key[2] == "run-a"
    assert (
        state.next_scenario_request("healthy", f"query-{MAX_LEDGER + 4}", "run-a") == 2
    )
    assert len(state.scenario_requests) == MAX_LEDGER


@pytest_asyncio.fixture
async def search_client():
    from agent.searxng_client import SearXNGClient

    fixture = create_app()
    client = await _client_for_app(SearXNGClient, fixture)
    yield client, fixture
    await client.close()


async def _client_for_app(client_type, fixture, timeout=0.05):
    client = client_type(base_url="http://fixture")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture),
        base_url="http://fixture",
        timeout=timeout,
    )
    return client


async def _start_tcp_fixture(default_scenario: str):
    """Run the fixture through Uvicorn so httpx exercises TCP timeouts."""
    from slopsearx_fixture.app import create_app

    fixture = create_app(default_scenario=default_scenario)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(fixture, host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=1) as probe_client:
            for _ in range(40):
                try:
                    if (
                        await probe_client.get(f"{base_url}/health")
                    ).status_code == 200:
                        return base_url, task, server
                except httpx.HTTPError:
                    await asyncio.sleep(0.025)
        raise AssertionError("fixture Uvicorn server did not become healthy")
    except BaseException:
        server.should_exit = True
        await task
        raise


async def _stop_tcp_fixture(
    base_url: str, task: asyncio.Task, server: uvicorn.Server
) -> None:
    del base_url
    server.should_exit = True
    await task


@pytest.mark.asyncio
async def test_health_and_versioned_healthy_response_are_real_http(search_client):
    client, fixture = search_client
    direct = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture), base_url="http://fixture"
    )
    response = await direct.get("/health")
    assert response.json() == {
        "status": "ok",
        "service": "slopsearx-fixture",
        "schema_version": SCHEMA_VERSION,
    }

    results, health = await client.search("fixture", limit=3)
    assert len(results) == 3
    assert health.degraded is False
    assert all(result["url"].startswith("http://test-site:8000/") for result in results)
    parsed_response = await direct.get(
        "/search", params={"q": "fixture", "scenario_version": SCHEMA_VERSION}
    )
    parsed = SearchResponse.model_validate(parsed_response.json())
    assert parsed.schema_version == SCHEMA_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_status", "detail", "expected_count"),
    [
        ("zero-results", 200, "no results", 0),
        ("partial-degraded", 200, "Degraded", 1),
        ("unauthorized", 401, "HTTP 401", 0),
        ("forbidden", 403, "HTTP 403", 0),
        ("server-error", 503, "HTTP 503", 0),
        ("malformed-json", 200, "failed", 0),
        ("variant-fields", 200, "Degraded", 1),
    ],
)
async def test_failure_and_response_shape_scenarios(
    scenario, expected_status, detail, expected_count
):
    from agent.searxng_client import SearXNGClient

    fixture = create_app(default_scenario=scenario)
    client = await _client_for_app(SearXNGClient, fixture)
    direct = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture), base_url="http://fixture"
    )
    response = await direct.get(
        "/search", params={"scenario": scenario, "q": "not stored"}
    )
    assert response.status_code == expected_status
    results, health = await client.search("not stored")
    assert len(results) == expected_count
    assert detail.lower() in health.detail.lower()
    await client.close()


@pytest.mark.asyncio
async def test_rate_limit_is_raised_or_degraded_over_real_http():
    from agent.exceptions import RetryableRateLimitError
    from agent.searxng_client import SearXNGClient

    fixture = create_app(default_scenario="rate-limit-retry-after")
    client = await _client_for_app(SearXNGClient, fixture)
    response = await client._client.get("/search")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "2"

    with pytest.raises(RetryableRateLimitError) as raised:
        await client.search("fixture", raise_on_rate_limit=True)
    assert raised.value.retry_after_seconds == 2.0

    await client.close()
    no_header = await _client_for_app(
        SearXNGClient, create_app(default_scenario="rate-limit-no-retry-after")
    )
    results, health = await no_header.search("fixture")
    assert results == []
    assert "429" in health.detail
    await no_header.close()


@pytest.mark.asyncio
async def test_quota_exhaustion_is_partitioned_by_query_on_one_app():
    from agent.exceptions import RetryableRateLimitError
    from agent.searxng_client import SearXNGClient

    fixture = create_app(default_scenario="quota-exhaustion")
    client = await _client_for_app(SearXNGClient, fixture)
    first_results, first_health = await client.search("partition-a")
    assert first_results
    assert first_health.degraded is False
    with pytest.raises(RetryableRateLimitError) as raised:
        await client.search("partition-a", raise_on_rate_limit=True)
    assert raised.value.retry_after_seconds == 2.0
    isolated_results, isolated_health = await client.search("partition-b")
    assert isolated_results
    assert isolated_health.degraded is False
    await client.close()


@pytest.mark.asyncio
async def test_pagination_delay_and_category_ledger_are_bounded_and_private(
    search_client,
):
    from agent.searxng_client import SearXNGClient

    client, fixture = search_client
    direct = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture), base_url="http://fixture"
    )
    page_one = await direct.get(
        "/search", params={"scenario": "pagination", "pageno": 1, "limit": 2}
    )
    page_two = await direct.get(
        "/search", params={"scenario": "pagination", "pageno": 2, "limit": 2}
    )
    assert page_one.json()["results"][0]["url"] != page_two.json()["results"][0]["url"]

    response = await direct.get(
        "/search",
        params={"q": "secret query", "categories": "news,science", "delay_ms": 1},
    )
    assert response.status_code == 200
    await client.search("secret query", sources=["news"], categories=["research"])
    ledger = (await direct.get("/ledger")).json()
    entry = ledger["entries"][-1]
    assert entry["categories"] == "news,science"
    assert entry["schema_version"] == SCHEMA_VERSION
    assert entry["status"] == 200
    assert "query" not in entry
    assert "secret query" not in str(ledger)

    base_url, server_task, server = await _start_tcp_fixture("delayed")
    try:
        timeout_client = SearXNGClient(base_url)
        await timeout_client._client.aclose()
        timeout_client._client = httpx.AsyncClient(base_url=base_url, timeout=0.01)
        results, health = await timeout_client.search("delayed")
        assert results == []
        assert health.detail == "SearXNG request timed out"
        await timeout_client.close()
    finally:
        await _stop_tcp_fixture(base_url, server_task, server)


@pytest.mark.asyncio
async def test_ledger_is_versioned_run_scoped_and_resettable():
    from httpx import ASGITransport, AsyncClient

    direct = AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://fixture"
    )
    await direct.get("/search", params={"q": "private", "run_id": "run-a"})
    await direct.get("/search", params={"q": "other", "run_id": "run-b"})
    ledger = (await direct.get("/ledger", params={"run_id": "run-a"})).json()
    assert ledger["fixture_version"] == "v3"
    assert {entry["run_id"] for entry in ledger["entries"]} == {"run-a"}
    assert "private" not in json.dumps(ledger)
    assert (await direct.post("/ledger/reset", params={"run_id": "run-a"})).json() == {
        "status": "ok"
    }
    assert (await direct.get("/ledger", params={"run_id": "run-a"})).json()[
        "entries"
    ] == []
    await direct.aclose()


@pytest.mark.asyncio
async def test_ledger_is_hard_bounded_and_run_reset_removes_quota_state():
    from httpx import ASGITransport, AsyncClient

    direct = AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://fixture"
    )
    for index in range(205):
        response = await direct.get(
            "/search", params={"q": f"query-{index}", "run_id": "run-a"}
        )
        assert response.status_code == 200
    entries = (await direct.get("/ledger")).json()["entries"]
    assert len(entries) == 200
    assert entries[0]["request_id"] == 6
    await direct.get(
        "/search",
        params={"scenario": "quota-exhaustion", "q": "same", "run_id": "run-a"},
    )
    assert (
        await direct.get(
            "/search",
            params={"scenario": "quota-exhaustion", "q": "same", "run_id": "run-b"},
        )
    ).status_code == 200
    await direct.post("/ledger/reset", params={"run_id": "run-a"})
    assert (await direct.get("/ledger", params={"run_id": "run-a"})).json()[
        "entries"
    ] == []
    assert (
        await direct.get(
            "/search",
            params={"scenario": "quota-exhaustion", "q": "same", "run_id": "run-a"},
        )
    ).status_code == 200
    await direct.aclose()


@pytest.mark.asyncio
async def test_run_reset_isolates_quota_counters_and_ledger():
    from httpx import ASGITransport, AsyncClient

    direct = AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://fixture"
    )
    params = {"scenario": "quota-exhaustion", "q": "same", "run_id": "run-a"}
    assert (await direct.get("/search", params=params)).status_code == 200
    assert (await direct.get("/search", params=params)).status_code == 429
    await direct.post("/ledger/reset", params={"run_id": "run-a"})
    assert (await direct.get("/search", params=params)).status_code == 200
    assert (await direct.get("/ledger", params={"run_id": "run-a"})).json()["entries"]
    await direct.aclose()


@pytest.mark.asyncio
async def test_deep_search_gracefully_degrades_fixture_rate_limit():
    from agent.research.search import run_deep_search

    base_url, server_task, server = await _start_tcp_fixture("rate-limit-retry-after")
    result = await run_deep_search(
        "rate-limited fixture", limit=3, searxng_url=base_url, llm_model="unused"
    )
    assert result == {
        "results": [],
        "query_variations": ["rate-limited fixture"],
    }
    await _stop_tcp_fixture(base_url, server_task, server)


@pytest.mark.asyncio
async def test_research_discovery_propagates_retryable_fixture_rate_limit():
    from agent.exceptions import RetryableRateLimitError
    from agent.research.discovery import _run_research_discover_and_scrape
    from agent.searxng_client import SearXNGClient

    base_url, server_task, server = await _start_tcp_fixture("rate-limit-retry-after")
    searxng = SearXNGClient(base_url)
    try:
        with pytest.raises(RetryableRateLimitError) as raised:
            await _run_research_discover_and_scrape(
                "retry fixture", urls=None, searxng=searxng, scraper=object()
            )
        assert raised.value.retry_after_seconds == 2.0
    finally:
        await searxng.close()
        await _stop_tcp_fixture(base_url, server_task, server)


@pytest.mark.asyncio
async def test_query_marker_selects_empty_results_without_changing_normal_queries(
    search_client,
):
    client, _fixture = search_client
    results, health = await client.search("[fixture:zero-results] unmatched", limit=5)
    assert results == []
    assert health.degraded is False
    ordinary, _ = await client.search("unmatched", limit=5)
    assert len(ordinary) == 5
    override, _ = await client.search(
        "[fixture:zero-results] unmatched", limit=3, scenario="healthy"
    )
    assert len(override) == 3
