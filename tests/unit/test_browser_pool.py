"""Deterministic lifecycle coverage for the opt-in browser process pool."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest


class _Manager:
    def __init__(self, controller):
        self.controller = controller
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.controller

    async def __aexit__(self, *_args):
        self.exited = True


class _Browser:
    def __init__(self, name):
        self.name = name
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


class _Context:
    def __init__(self, proxy):
        self.proxy = proxy
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1

    async def new_page(self):
        return _Page()

    async def cookies(self):
        return []


class _Page:
    url = "https://one.example/a"

    async def goto(self, *_args, **_kwargs):
        return None

    async def title(self):
        return "Article"

    async def content(self):
        return "<html><body><article>content</article></body></html>"

    async def evaluate(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, _timeout):
        return None


@pytest.fixture
def fake_pool(monkeypatch):
    import scraper.browser_pool as browser_pool

    controller = object()
    manager = _Manager(controller)
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: manager),
    )
    browsers = []
    contexts = []

    async def launch(_playwright, url):
        browser = _Browser(url)
        browsers.append((url, browser))
        return browser, True

    async def context(browser, *, cloakbrowser, **kwargs):
        assert cloakbrowser is True
        value = _Context(kwargs.get("proxy"))
        contexts.append((browser, value))
        return value

    monkeypatch.setattr(browser_pool, "create_stealth_browser", launch)
    monkeypatch.setattr(browser_pool, "create_stealth_context", context)
    monkeypatch.setattr(browser_pool, "fingerprint_seed", lambda url: url.split("/")[2])
    return browser_pool, manager, browsers, contexts


@pytest.mark.asyncio
async def test_reuses_domain_process_with_fresh_proxy_contexts(fake_pool):
    browser_pool, manager, browsers, contexts = fake_pool
    pool = browser_pool.BrowserPool(
        enabled=True, max_processes=2, idle_ttl=60, max_age=900
    )

    first = await pool.acquire("https://one.example/a", {"server": "proxy-a"})
    second = await pool.acquire("https://one.example/b", {"server": "proxy-b"})

    assert [url for url, _ in browsers] == ["https://one.example/a"]
    assert contexts[0][0] is contexts[1][0]
    assert contexts[0][1] is not contexts[1][1]
    assert contexts[0][1].proxy == {"server": "proxy-a"}
    assert contexts[1][1].proxy == {"server": "proxy-b"}

    await first.release()
    await second.release()
    assert contexts[0][1].close_calls == contexts[1][1].close_calls == 1
    await pool.close()
    assert browsers[0][1].close_calls == 1
    assert manager.exited


@pytest.mark.asyncio
async def test_domain_fingerprint_and_process_bound(fake_pool):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True, max_processes=1)
    first = await pool.acquire("https://one.example/a")

    pending = asyncio.create_task(pool.acquire("https://two.example/a"))
    await asyncio.sleep(0)
    assert not pending.done()
    await first.release()
    second = await asyncio.wait_for(pending, timeout=1)

    assert [url for url, _ in browsers] == [
        "https://one.example/a",
        "https://two.example/a",
    ]
    await second.release()
    await pool.close()


@pytest.mark.asyncio
async def test_idle_expiry_recycles_process(fake_pool):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True, idle_ttl=0, max_age=900)
    first = await pool.acquire("https://one.example/a")
    old_browser = first.entry.browser
    await first.release()
    second = await pool.acquire("https://one.example/b")

    assert len(browsers) == 2
    assert old_browser.close_calls == 1
    await second.release()
    await pool.close()


@pytest.mark.asyncio
async def test_context_creation_failure_closes_new_process(fake_pool, monkeypatch):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True)

    async def fail_context(*_args, **_kwargs):
        raise RuntimeError("context failed")

    monkeypatch.setattr(browser_pool, "create_stealth_context", fail_context)
    with pytest.raises(RuntimeError, match="context failed"):
        await pool.acquire("https://one.example/a")

    assert pool.process_count == 0
    assert browsers[0][1].close_calls == 1
    await pool.close()


@pytest.mark.asyncio
async def test_unhealthy_lease_recycles_process(fake_pool):
    browser_pool, _manager, browsers, contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True)
    lease = await pool.acquire("https://one.example/a")

    await lease.release(healthy=False)

    assert pool.process_count == 0
    assert browsers[0][1].close_calls == 1
    assert contexts[0][1].close_calls == 1
    await pool.close()


@pytest.mark.asyncio
async def test_cancelled_release_finishes_context_cleanup(fake_pool):
    browser_pool, _manager, _browsers, contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True)
    lease = await pool.acquire("https://one.example/a")
    started = asyncio.Event()
    finish = asyncio.Event()

    async def delayed_close():
        started.set()
        await finish.wait()
        contexts[0][1].close_calls += 1

    contexts[0][1].close = delayed_close
    cleanup = asyncio.create_task(lease.release())
    await started.wait()
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup
    finish.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert lease.entry.leases == 0
    assert lease.context not in (lease.entry.contexts or set())
    await pool.close()


@pytest.mark.asyncio
async def test_fetch_tiers_uses_pool_context_and_releases_it(fake_pool, monkeypatch):
    browser_pool, _manager, _browsers, contexts = fake_pool
    import scraper.captcha as captcha
    import scraper.cookie_store as cookie_store
    import scraper.fetch_tiers as tiers

    pool = browser_pool.BrowserPool(enabled=True)
    monkeypatch.setattr(browser_pool, "_browser_pool", pool)
    monkeypatch.setattr(tiers, "html_to_markdown", lambda _html: "content " * 100)

    async def no_retry(function, *args, **kwargs):
        return await function(*args, **kwargs)

    monkeypatch.setattr(tiers, "retry_transient", no_retry)

    async def no_cookie_io(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cookie_store, "inject_cookies", no_cookie_io)
    monkeypatch.setattr(cookie_store, "store_cookies", no_cookie_io)

    async def no_captcha(_page, _url):
        return None, []

    monkeypatch.setattr(captcha, "resolve_captcha", no_captcha)
    result = await tiers._playwright_fetch_unbounded(
        "https://one.example/a", {"server": "proxy-a"}
    )

    assert result["source"] == "playwright"
    assert contexts[0][1].proxy == {"server": "proxy-a"}
    assert contexts[0][1].close_calls == 1
    assert pool.process_count == 1
    await pool.close()


@pytest.mark.asyncio
async def test_recycling_counts_slow_process_close_against_capacity(fake_pool):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True, max_processes=1, max_age=0)
    first = await pool.acquire("https://one.example/a")
    await first.release()

    started = asyncio.Event()
    finish = asyncio.Event()

    async def delayed_close():
        started.set()
        await finish.wait()

    browsers[0][1].close = delayed_close
    reaper = asyncio.create_task(pool._reap_expired())
    await started.wait()
    assert pool.process_count == 1

    pending = asyncio.create_task(pool.acquire("https://two.example/a"))
    await asyncio.sleep(0)
    assert not pending.done()
    finish.set()
    await reaper
    second = await asyncio.wait_for(pending, timeout=1)
    assert pool.process_count == 1
    await second.release()
    await pool.close()


@pytest.mark.asyncio
async def test_unhealthy_lease_retires_process_after_other_lease_finishes(fake_pool):
    browser_pool, _manager, browsers, contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True, max_processes=1)
    first = await pool.acquire("https://one.example/a")
    second = await pool.acquire("https://one.example/b")

    await first.release(healthy=False)
    assert browsers[0][1].close_calls == 0
    assert contexts[1][1].close_calls == 0
    assert pool.process_count == 1

    await second.release()
    assert browsers[0][1].close_calls == 1
    assert contexts[1][1].close_calls == 1
    assert pool.process_count == 0
    await pool.close()


@pytest.mark.asyncio
async def test_launch_cancellation_closes_native_browser(fake_pool):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True)
    context_started = asyncio.Event()

    async def wait_for_context(*_args, **_kwargs):
        context_started.set()
        await asyncio.Future()

    browser_pool.create_stealth_context = wait_for_context
    task = asyncio.create_task(pool.acquire("https://one.example/a"))
    await context_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert browsers[0][1].close_calls == 1
    assert pool.process_count == 0
    await pool.close()


@pytest.mark.asyncio
async def test_cancellation_during_native_launch_closes_result(fake_pool):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True)
    launch_started = asyncio.Event()
    release_launch = asyncio.Event()

    async def delayed_launch(_playwright, url):
        browser = _Browser(url)
        browsers.append((url, browser))
        launch_started.set()
        await release_launch.wait()
        return browser, True

    browser_pool.create_stealth_browser = delayed_launch
    task = asyncio.create_task(pool.acquire("https://one.example/a"))
    await launch_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_launch.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert browsers[0][1].close_calls == 1
    assert pool.process_count == 0
    await pool.close()


@pytest.mark.asyncio
async def test_close_waits_for_inflight_launch_and_allows_lifecycle_recreation(
    fake_pool,
):
    browser_pool, manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True)
    launch_started = asyncio.Event()
    release_launch = asyncio.Event()

    async def delayed_launch(_playwright, url):
        launch_started.set()
        await release_launch.wait()
        browser = _Browser(url)
        browsers.append((url, browser))
        return browser, True

    browser_pool.create_stealth_browser = delayed_launch
    acquiring = asyncio.create_task(pool.acquire("https://one.example/a"))
    await launch_started.wait()
    closing = asyncio.create_task(pool.close())
    await asyncio.sleep(0)
    assert not closing.done()
    release_launch.set()
    with pytest.raises(RuntimeError, match="closed"):
        await acquiring
    await closing
    assert browsers[-1][1].close_calls == 1
    assert manager.exited

    await pool.start()
    assert pool._started
    await pool.close()


@pytest.mark.asyncio
async def test_cancelled_reaper_finishes_every_detached_process(fake_pool):
    browser_pool, _manager, browsers, _contexts = fake_pool
    pool = browser_pool.BrowserPool(enabled=True, max_processes=2, idle_ttl=60)
    first = await pool.acquire("https://one.example/a")
    second = await pool.acquire("https://two.example/a")
    await first.release()
    await second.release()
    pool.idle_ttl = 0
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed_close():
        started.set()
        await finish.wait()
        browsers[0][1].close_calls += 1

    browsers[0][1].close = delayed_close
    reaper = asyncio.create_task(pool._reap_expired())
    await started.wait()
    reaper.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await reaper
    assert all(browser.close_calls == 1 for _, browser in browsers)
    assert pool.process_count == 0
    await asyncio.wait_for(pool.close(), 1)
