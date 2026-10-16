"""Durable terminal callbacks; repeated attempts preserve one exact event ID/body."""

import asyncio
from contextlib import suppress
from uuid import uuid4

from fastapi import Request

from ..webhook import deliver_recorded_webhook
from .durable_research import DurableResearchLedger


async def deliver_notifications(ledger: DurableResearchLedger, cursor: int = 0) -> int:
    cursor, run_ids = await asyncio.to_thread(ledger.notification_candidates, cursor)
    for run_id in run_ids:
        owner = str(uuid4())
        delivery = await asyncio.to_thread(ledger.claim_notification, run_id, owner)
        if delivery is None:
            continue
        url, body = delivery
        outcome = await deliver_recorded_webhook(url, body)
        await asyncio.to_thread(ledger.finish_notification, run_id, owner, outcome)
    return cursor


async def run_notification_loop(app, shutdown: asyncio.Event) -> None:
    from ..routes.experimental_research import _durable_ledger

    ledger = _durable_ledger(Request({"type": "http", "app": app}))
    cursor = 0
    while not shutdown.is_set():
        try:
            cursor = await deliver_notifications(ledger, cursor)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A crashed attempt's bounded lease expires; retries use exactly
            # the same body/event ID. Never reinterpret it as a paid model call.
            pass
        with suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=0.5)
