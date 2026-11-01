"""Focused tests for CrawlEngine cancellation teardown (ADR-0051).

These tests run in the Fast Tests lane (in-process) and drive the changed
cancellation lines in ``agent-svc/agent/crawler.py``: the token checks in
``run()``/``_scrape_url``, the Redis cancelled-status check, and the
deterministic ``_cancel_and_await_tasks`` teardown.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_cancel_and_await_tasks_awaits_pending_tasks():
    from agent.crawler import CrawlEngine, CrawlOptions

    engine = CrawlEngine(MagicMock(), options=CrawlOptions(max_pages=10))

    started = asyncio.Event()

    async def _pending_work() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_pending_work())
    await started.wait()
    engine._pending_tasks.add(task)

    await engine._cancel_and_await_tasks()

    assert task.done()  # awaited, not left pending
    assert not engine._pending_tasks


@pytest.mark.asyncio
async def test_run_finally_awaits_children_on_forced_cancel():
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()
    started = asyncio.Event()

    async def _scrape(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)
        return {"success": True, "data": {"markdown": "# x", "metadata": {}}}

    scraper.scrape = AsyncMock(side_effect=_scrape)

    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(max_pages=1, max_depth=0, sitemap_mode="skip"),
    )

    with patch.object(engine, "_get_html", return_value=""):
        # Cancel the engine mid-run via a task to exercise the finally teardown.
        task = asyncio.create_task(engine.run("http://example.com/"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # The engine must have cleaned up its internal HTML client and tasks.
    assert engine._html_client is None
    assert not engine._pending_tasks


@pytest.mark.asyncio
async def test_run_raises_when_token_set_before_run():
    from agent.cancel import JobCancelledError, cancel_scope
    from agent.crawler import CrawlEngine, CrawlOptions

    engine = CrawlEngine(
        MagicMock(),
        options=CrawlOptions(max_pages=1, max_depth=0, sitemap_mode="skip"),
    )
    token = asyncio.Event()
    token.set()

    with cancel_scope(token):
        with pytest.raises(JobCancelledError):
            await engine.run("http://example.com/")


@pytest.mark.asyncio
async def test_run_raises_when_token_set_mid_crawl():
    from agent.cancel import JobCancelledError, cancel_scope
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()
    token = asyncio.Event()

    async def _scrape(url, **kwargs):
        token.set()  # cancel mid-crawl, after the start URL is scraped
        return {"success": True, "data": {"markdown": "# x", "metadata": {}}}

    scraper.scrape = AsyncMock(side_effect=_scrape)

    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(max_pages=10, max_depth=1, sitemap_mode="skip"),
    )

    with patch.object(
        engine, "_get_html", return_value='<a href="http://example.com/child">c</a>'
    ):
        with cancel_scope(token):
            with pytest.raises(JobCancelledError):
                await engine.run("http://example.com/")

    assert not engine._pending_tasks
    assert engine._html_client is None


@pytest.mark.asyncio
async def test_run_stops_when_store_reports_cancelled():
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()
    scraper.scrape = AsyncMock(
        return_value={"success": True, "data": {"markdown": "# x", "metadata": {}}}
    )

    store = MagicMock()
    store.get_job.return_value = {"status": "cancelled"}
    store.increment_completed = MagicMock()
    store.update_job_progress = MagicMock()

    engine = CrawlEngine(
        scraper,
        store=store,
        options=CrawlOptions(max_pages=10, max_depth=0, sitemap_mode="skip"),
    )
    result = await engine.run("http://example.com/", job_id="job-1")

    assert result.completed == 0
    assert result.pages == []
    scraper.scrape.assert_not_called()


@pytest.mark.asyncio
async def test_run_stops_at_max_pages_and_updates_store():
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()

    async def _scrape(url, **kwargs):
        return {"success": True, "data": {"markdown": "# x", "metadata": {}}}

    scraper.scrape = AsyncMock(side_effect=_scrape)

    store = MagicMock()
    store.get_job.return_value = {"status": "processing"}
    store.increment_completed = MagicMock()
    store.update_job_progress = MagicMock()

    engine = CrawlEngine(
        scraper,
        store=store,
        options=CrawlOptions(max_pages=1, max_depth=1, sitemap_mode="skip"),
    )
    engine._update_interval = 0

    with patch.object(
        engine, "_get_html", return_value='<a href="http://example.com/child">c</a>'
    ):
        result = await engine.run("http://example.com/", job_id="job-1")

    assert result.completed == 1
    assert len(result.pages) == 1
    # Periodic progress update ran during the crawl, plus the final update.
    assert store.update_job_progress.call_count >= 2


@pytest.mark.asyncio
async def test_run_stops_when_cancel_flag_set():
    from agent.crawler import CrawlEngine, CrawlOptions

    engine = CrawlEngine(
        MagicMock(),
        options=CrawlOptions(max_pages=10, max_depth=0, sitemap_mode="skip"),
    )
    engine._cancel_flag = True

    result = await engine.run("http://example.com/")

    assert result.completed == 0
    assert result.pages == []


@pytest.mark.asyncio
async def test_run_filters_all_urls_breaks_dispatch():
    from agent.crawler import CrawlEngine, CrawlOptions

    engine = CrawlEngine(
        MagicMock(),
        options=CrawlOptions(
            max_pages=10,
            max_depth=0,
            sitemap_mode="skip",
            include_paths=["/nonexistent/*"],
        ),
    )
    result = await engine.run("http://example.com/")

    assert result.completed == 0
    assert result.pages == []


@pytest.mark.asyncio
async def test_run_start_url_failure_returns_result():
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()
    scraper.scrape = AsyncMock(
        return_value={"success": False, "error": "Connection refused"}
    )

    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(max_pages=10, max_depth=0, sitemap_mode="skip"),
    )
    result = await engine.run("http://example.com/")

    assert result.completed == 0
    assert result.pages == []
    assert len(result.errors) == 1
    assert result.errors[0]["error"] == "Connection refused"


@pytest.mark.asyncio
async def test_run_raises_timeout_when_max_duration_exceeded(monkeypatch):
    from agent.crawler import CrawlEngine, CrawlOptions

    # Deterministic clock: every reading advances 10s, so the first
    # elapsed-time check already exceeds the 5s cap regardless of call order.
    ticks = {"now": 0.0}

    def _clock() -> float:
        ticks["now"] += 10.0
        return ticks["now"]

    monkeypatch.setattr("agent.crawler.time.monotonic", _clock)

    scraper = MagicMock()
    scraper.scrape = AsyncMock(
        return_value={"success": True, "data": {"markdown": "# x", "metadata": {}}}
    )
    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(
            max_pages=10, max_depth=1, sitemap_mode="skip", max_duration_seconds=5
        ),
    )
    with pytest.raises(TimeoutError):
        await engine.run("http://example.com/")


@pytest.mark.asyncio
async def test_zero_max_duration_disables_the_wall_clock_cap():
    """ADR-0077: 0 means unbounded; the idle timeout is the stall detector."""
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()
    scraper.scrape = AsyncMock(
        return_value={"success": True, "data": {"markdown": "# x", "metadata": {}}}
    )
    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(
            max_pages=1, max_depth=0, sitemap_mode="skip", max_duration_seconds=0
        ),
    )
    result = await engine.run("http://example.com/")
    assert len(result.pages) == 1


@pytest.mark.asyncio
async def test_run_success_closes_html_client():
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()

    async def _scrape(url, **kwargs):
        return {"success": True, "data": {"markdown": "# x", "metadata": {}}}

    scraper.scrape = AsyncMock(side_effect=_scrape)

    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(max_pages=1, max_depth=0, sitemap_mode="skip"),
    )
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock()
    engine._html_client = fake_client

    with patch.object(engine, "_get_html", return_value="<html></html>"):
        result = await engine.run("http://example.com/")

    assert result.completed == 1
    assert len(result.pages) == 1
    fake_client.aclose.assert_awaited_once()
    assert engine._html_client is None


@pytest.mark.asyncio
async def test_scrape_url_checks_token_inside_semaphore():
    from agent.cancel import JobCancelledError, cancel_scope
    from agent.crawler import CrawlEngine, CrawlOptions

    scraper = MagicMock()
    scraper.scrape = AsyncMock()

    engine = CrawlEngine(
        scraper,
        options=CrawlOptions(max_pages=1, max_depth=0, sitemap_mode="skip"),
    )
    token = asyncio.Event()
    token.set()

    with cancel_scope(token):
        with pytest.raises(JobCancelledError):
            await engine._scrape_url(
                url="http://example.com/",
                depth=0,
                from_sitemap=False,
                base_domain="example.com",
                page_callback=None,
                error_callback=None,
                job_id=None,
            )

    scraper.scrape.assert_not_called()


@pytest.mark.asyncio
async def test_close_awaits_pending_tasks():
    from agent.crawler import CrawlEngine, CrawlOptions

    engine = CrawlEngine(MagicMock(), options=CrawlOptions(max_pages=10))
    started = asyncio.Event()

    async def _work():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_work())
    await started.wait()
    engine._pending_tasks.add(task)

    await engine.close()

    assert task.done()
    assert not engine._pending_tasks
    assert engine._html_client is None


@pytest.mark.asyncio
async def test_cancel_sets_flag_and_cancels_pending_tasks():
    from agent.crawler import CrawlEngine, CrawlOptions

    engine = CrawlEngine(MagicMock(), options=CrawlOptions(max_pages=10))
    started = asyncio.Event()

    async def _work():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_work())
    await started.wait()
    engine._pending_tasks.add(task)

    engine.cancel()

    assert engine._cancel_flag is True
    with pytest.raises(asyncio.CancelledError):
        await task
    engine._pending_tasks.discard(task)
