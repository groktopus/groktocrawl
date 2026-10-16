"""Cancelling real answer context construction stops its source scanner."""

import asyncio
from threading import Event

import pytest
from agent.research.discovery import _build_answer_context
from agent.research.evidence import run_evidence_builder_async
from agent.research.sources import SourceArtifact


@pytest.mark.asyncio
async def test_answer_context_cancellation_signals_real_selector(monkeypatch):
    import agent.research.evidence as evidence

    started, stopped = Event(), Event()

    def scanner(_text, _query, _budget, cancelled=None):
        assert cancelled is not None
        started.set()
        assert cancelled.wait(3), "Answer scan did not receive cancellation"
        stopped.set()
        raise InterruptedError("Evidence selection cancelled")

    monkeypatch.setattr(evidence, "select_passages", scanner)
    artifact = SourceArtifact(
        "https://example.com/source", markdown="retained source text " * 5000
    )
    task = asyncio.create_task(
        run_evidence_builder_async(_build_answer_context, [], [artifact], "source", 256)
    )
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(stopped.wait, 3)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
