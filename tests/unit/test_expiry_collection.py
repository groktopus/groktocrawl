"""Collection limits and cancellation semantics without a database."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent.experimental.expiry_store import ExpiryStore


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 21, -1, True, 1.5, "2", None])
async def test_invalid_limit_never_opens_database(limit):
    with patch.object(
        ExpiryStore, "_transaction", side_effect=AssertionError("database accessed")
    ):
        with pytest.raises(ValueError):
            await ExpiryStore().collect_expired(uuid4(), limit)


@pytest.mark.asyncio
async def test_cancelled_pass_preserves_cancellation_and_uncertainty_note():
    entered = asyncio.Event()

    class Waiting(ExpiryStore):
        async def _expiry_candidates(self, scope, limit):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(Waiting().collect_expired(uuid4()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as error:
        await task
    assert "committed earlier candidates" in error.value.__notes__[0]


@pytest.mark.asyncio
async def test_total_deadline_bounds_discovery():
    class Waiting(ExpiryStore):
        async def _expiry_candidates(self, scope, limit):
            await asyncio.Event().wait()

    real_timeout = asyncio.timeout
    with patch(
        "agent.experimental.expiry_store.asyncio.timeout",
        side_effect=lambda _: real_timeout(0.01),
    ):
        with pytest.raises(TimeoutError) as error:
            await Waiting().collect_expired(uuid4())
    assert "committed earlier candidates" in error.value.__notes__[0]
