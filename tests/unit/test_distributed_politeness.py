"""Opt-in distributed pacing must fail closed without shared coordination."""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from scraper.politeness import PolitenessManager, _DomainState

from tests.outcome_governance import governed_skip


def manager():
    value = PolitenessManager()
    value._enabled = True
    value._distributed = True
    value._domains["example.org"] = _DomainState(
        robots_cached_at=time.time(), crawl_delay=0.5
    )
    return value


@pytest.mark.asyncio
async def test_replicas_use_same_atomic_origin_key(monkeypatch):
    client = SimpleNamespace(eval=AsyncMock(side_effect=[0, 500, 1000]))
    monkeypatch.setattr(
        "scraper.cache._get_cache_client", AsyncMock(return_value=client)
    )
    replicas = [manager() for _ in range(3)]
    results = await asyncio.gather(
        *(r.check("https://example.org/a") for r in replicas)
    )
    assert [r.delay_seconds for r in results] == [0, 0.5, 1]
    calls = client.eval.call_args_list
    assert len({call.args[2] for call in calls}) == 1
    assert all(call.args[1] == 1 and call.args[3:] == (500, 30000) for call in calls)


@pytest.mark.asyncio
async def test_readonly_tier_does_not_reserve_again(monkeypatch):
    client = SimpleNamespace(eval=AsyncMock(return_value=0))
    monkeypatch.setattr(
        "scraper.cache._get_cache_client", AsyncMock(return_value=client)
    )
    result = await manager().check("https://example.org/a", rate_limit=False)
    assert result.action == "proceed"
    client.eval.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, -1, ConnectionError("offline")])
async def test_missing_capacity_or_coordination_blocks(monkeypatch, response):
    client = None if response is None else SimpleNamespace(eval=AsyncMock())
    if client:
        if isinstance(response, Exception):
            client.eval.side_effect = response
        else:
            client.eval.return_value = response
    monkeypatch.setattr(
        "scraper.cache._get_cache_client", AsyncMock(return_value=client)
    )
    assert (await manager().check("https://example.org/a")).action == "blocked"


@pytest.mark.asyncio
async def test_cancellation_propagates(monkeypatch):
    client = SimpleNamespace(eval=AsyncMock(side_effect=asyncio.CancelledError()))
    monkeypatch.setattr(
        "scraper.cache._get_cache_client", AsyncMock(return_value=client)
    )
    with pytest.raises(asyncio.CancelledError):
        await manager().check("https://example.org/a")


@pytest.mark.asyncio
async def test_valkey_reservation_survives_cancelled_wait(monkeypatch):
    """The real Lua reservation keeps its slot after a caller is cancelled."""
    redis_url = os.environ.get("TEST_VALKEY_URL")
    if not redis_url:
        governed_skip(
            "TEST_VALKEY_URL is not configured",
            owner="repository-maintainer",
            issue="#629",
            classification="retained",
            environment="Requires isolated Valkey; exercised in scraper-scaleout CI",
        )

    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=True)
    value = manager()
    value._domains["example.org"].crawl_delay = 0.05
    key = value._rate_key("example.org") + ":slots"
    await client.delete(key)
    monkeypatch.setattr(
        "scraper.cache._get_cache_client", AsyncMock(return_value=client)
    )
    try:
        assert (await value.check("https://example.org/first")).action == "proceed"
        delayed = await value.check("https://example.org/second")
        assert delayed.action == "delay"
        assert delayed.delay_seconds >= 0.04

        # Cancel the actual delayed caller. Its local rollback cannot reclaim
        # the shared Lua slot after another replica could have reserved later.
        delayed_task = asyncio.create_task(asyncio.sleep(delayed.delay_seconds))
        await asyncio.sleep(0.01)
        delayed_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delayed_task
        value.rollback_reservation("https://example.org/second")
        assert float(await client.get(key)) > time.time() * 1000
        assert await client.pttl(key) > 0

        later = await value.check("https://example.org/third")
        assert later.action == "delay"
        assert later.delay_seconds >= 0.08
    finally:
        await client.delete(key)
        await client.aclose()
