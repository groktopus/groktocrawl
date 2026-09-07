"""Bounded, opt-in Playwright browser process reuse.

The pool deliberately keys processes by the domain fingerprint used by
``create_stealth_browser``.  A request always gets a new context, so cookies,
proxy settings, and pages cannot cross request boundaries while the expensive
browser process remains warm.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from .stealth import create_stealth_browser, create_stealth_context, fingerprint_seed

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using %s", name, default)
        return default


@dataclass
class _BrowserEntry:
    browser: Any
    cloakbrowser: bool
    fingerprint: str
    created_at: float
    last_used: float
    leases: int = 0
    contexts: set[Any] | None = None
    retiring: bool = False
    close_started: bool = False


@dataclass
class BrowserLease:
    """A fresh request context leased from a pooled browser."""

    pool: BrowserPool
    entry: _BrowserEntry
    context: Any
    _released: bool = False

    async def release(self, healthy: bool = True) -> None:
        if self._released:
            return
        self._released = True
        await self.pool.release(self, healthy=healthy)


class BrowserPool:
    """A small lifecycle-managed pool of domain-fingerprinted browsers."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_processes: int = 2,
        idle_ttl: float = 60.0,
        max_age: float = 900.0,
    ) -> None:
        self.enabled = enabled
        self.max_processes = max(1, int(max_processes))
        self.idle_ttl = max(0.0, float(idle_ttl))
        self.max_age = max(0.0, float(max_age))
        self._condition = asyncio.Condition()
        self._entries: list[_BrowserEntry] = []
        self._launching = 0
        self._closing_processes = 0
        self._playwright_manager: Any = None
        self._playwright: Any = None
        self._reaper_task: asyncio.Task[None] | None = None
        self._started = False
        self._closing = False

    @classmethod
    def from_env(cls) -> BrowserPool:
        return cls(
            enabled=_env_bool("SCRAPER_BROWSER_POOL_ENABLED"),
            max_processes=_env_int("SCRAPER_BROWSER_POOL_MAX_PROCESSES", 2),
            idle_ttl=_env_float("SCRAPER_BROWSER_POOL_IDLE_TTL_SECONDS", 60.0),
            max_age=_env_float("SCRAPER_BROWSER_POOL_MAX_AGE_SECONDS", 900.0),
        )

    @property
    def process_count(self) -> int:
        """Return all processes still occupying the bounded pool capacity."""
        return len(self._entries) + self._launching + self._closing_processes

    async def _close_entry(self, entry: _BrowserEntry) -> None:
        """Close one detached entry and release its capacity reservation."""
        try:
            for context in list(entry.contexts or ()):
                try:
                    await context.close()
                except Exception:
                    logger.debug("Browser context cleanup failed", exc_info=True)
            try:
                await entry.browser.close()
            except Exception:
                logger.debug("Browser process cleanup failed", exc_info=True)
        finally:
            async with self._condition:
                self._closing_processes = max(0, self._closing_processes - 1)
                self._condition.notify_all()

    async def _close_entry_safely(self, entry: _BrowserEntry) -> None:
        """Finish native cleanup even when the caller is cancelled."""
        task = asyncio.create_task(
            self._close_entry(entry), name="scraper-browser-pool-close"
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _retire(self, entry: _BrowserEntry, *, release_lease: bool) -> None:
        """Retire an entry without interrupting leases still using it."""
        close_now = False
        async with self._condition:
            if release_lease:
                entry.leases = max(0, entry.leases - 1)
            entry.retiring = True
            if entry in self._entries and entry.leases == 0:
                self._entries.remove(entry)
                entry.close_started = True
                self._closing_processes += 1
                close_now = True
            self._condition.notify_all()
        if close_now:
            await self._close_entry_safely(entry)

    async def start(self) -> None:
        """Start one shared Playwright controller, if pooling is enabled."""
        if not self.enabled or self._started:
            return
        async with self._condition:
            if self._started:
                return
            if self._closing:
                if self._entries or self._launching or self._closing_processes:
                    raise RuntimeError("browser pool is closed")
                # A process can host more than one application lifespan in
                # tests and in reloaders. Recreate the controller after the
                # previous lifespan has fully drained.
                self._closing = False
            from playwright.async_api import async_playwright

            manager = async_playwright()
            try:
                playwright = await manager.__aenter__()
            except BaseException:
                await manager.__aexit__(None, None, None)
                raise
            self._playwright_manager = manager
            self._playwright = playwright
            self._started = True
            self._reaper_task = asyncio.create_task(
                self._reap_loop(), name="scraper-browser-pool-reaper"
            )
        logger.info(
            "Browser pool started (max_processes=%d idle_ttl=%.1fs max_age=%.1fs)",
            self.max_processes,
            self.idle_ttl,
            self.max_age,
        )

    async def acquire(self, url: str, proxy: dict | None = None) -> BrowserLease:
        """Lease a fresh context, waiting while the process bound is full."""
        if not self.enabled:
            raise RuntimeError("browser pool is disabled")
        await self.start()
        fingerprint = fingerprint_seed(url)
        entry: _BrowserEntry | None = None

        while True:
            await self._reap_expired()
            retire: _BrowserEntry | None = None
            async with self._condition:
                if self._closing:
                    raise RuntimeError("browser pool is closed")
                entry = next(
                    (
                        item
                        for item in self._entries
                        if item.fingerprint == fingerprint and not item.retiring
                    ),
                    None,
                )
                if entry is not None:
                    entry.leases += 1
                    break
                if self.process_count < self.max_processes:
                    self._launching += 1
                    break
                # A different-domain request cannot use an idle process with
                # the wrong fingerprint. Recycle one immediately so the
                # bounded pool does not wait forever for its own capacity.
                retire = next(
                    (item for item in self._entries if item.leases == 0), None
                )
                if retire is None:
                    await self._condition.wait()
                else:
                    self._entries.remove(retire)
                    retire.retiring = True
                    retire.close_started = True
                    self._closing_processes += 1
                    self._condition.notify_all()

            # The condition is notified by release/recycle. Re-check expiry and
            # availability in the next iteration rather than holding the lock.
            if retire is not None:
                await self._close_entry_safely(retire)

        if entry is None:
            browser = None
            launching = True
            registered = False
            close_after_registration = False
            try:
                launch_task = asyncio.create_task(
                    create_stealth_browser(self._playwright, url),
                    name="scraper-browser-pool-launch",
                )
                try:
                    browser, cloakbrowser = await asyncio.shield(launch_task)
                except asyncio.CancelledError:
                    # Shield the native launch so cancellation cannot strand
                    # a browser that was created just before the await ended.
                    with contextlib.suppress(BaseException):
                        browser, _ = await launch_task
                    if browser is not None:
                        with contextlib.suppress(Exception):
                            await browser.close()
                    browser = None
                    raise
                now = time.monotonic()
                entry = _BrowserEntry(
                    browser=browser,
                    cloakbrowser=cloakbrowser,
                    fingerprint=fingerprint,
                    created_at=now,
                    last_used=now,
                    leases=1,
                    contexts=set(),
                )
                async with self._condition:
                    self._launching -= 1
                    launching = False
                    if self._closing:
                        entry.retiring = True
                        entry.close_started = True
                        self._closing_processes += 1
                        close_after_registration = True
                    else:
                        self._entries.append(entry)
                        registered = True
                    self._condition.notify_all()
                if close_after_registration:
                    await self._close_entry_safely(entry)
                    raise RuntimeError("browser pool is closed")
            except BaseException:
                try:
                    if (
                        browser is not None
                        and not registered
                        and not close_after_registration
                    ):
                        with contextlib.suppress(Exception):
                            await browser.close()
                finally:
                    if launching:
                        async with self._condition:
                            self._launching = max(0, self._launching - 1)
                            self._condition.notify_all()
                raise

        context = None
        context_registered = False
        try:
            context_kwargs = {"proxy": proxy} if proxy else {}
            context = await create_stealth_context(
                entry.browser,
                cloakbrowser=entry.cloakbrowser,
                **context_kwargs,
            )
            async with self._condition:
                recycled = entry not in self._entries
                if not recycled:
                    if entry.contexts is None:
                        entry.contexts = set()
                    entry.contexts.add(context)
                    context_registered = True
            if recycled:
                await context.close()
                context = None
                raise RuntimeError("browser process was recycled during setup")
            return BrowserLease(self, entry, context)
        except BaseException:
            if context is not None and not context_registered:
                with contextlib.suppress(Exception):
                    await context.close()
            await self._retire(entry, release_lease=True)
            raise

    async def release(self, lease: BrowserLease, *, healthy: bool = True) -> None:
        """Close the request context and return or recycle its browser."""

        async def cleanup() -> None:
            context_closed = False
            try:
                await lease.context.close()
            except Exception:
                healthy_local = False
                logger.debug("Browser context cleanup failed", exc_info=True)
            else:
                context_closed = True
                healthy_local = healthy
            async with self._condition:
                if context_closed or healthy_local:
                    if lease.entry.contexts is not None:
                        lease.entry.contexts.discard(lease.context)
                lease.entry.leases = max(0, lease.entry.leases - 1)
                close_now = False
                if not healthy_local:
                    # Retire this process, but let other leases finish using
                    # their own contexts before closing the shared browser.
                    lease.entry.retiring = True
                if (
                    lease.entry in self._entries
                    and lease.entry.retiring
                    and lease.entry.leases == 0
                ):
                    self._entries.remove(lease.entry)
                    lease.entry.close_started = True
                    self._closing_processes += 1
                    close_now = True
                elif lease.entry in self._entries:
                    lease.entry.last_used = time.monotonic()
                self._condition.notify_all()
            if close_now:
                await self._close_entry(lease.entry)

        task = asyncio.create_task(cleanup(), name="scraper-browser-context-cleanup")
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Let the cleanup task finish after request cancellation. Browser
            # processes are otherwise left busy until the next acquisition.
            task.add_done_callback(lambda done: done.exception())
            raise

    async def _discard(self, entry: _BrowserEntry) -> None:
        """Retire an entry after an acquire-side setup failure."""
        await self._retire(entry, release_lease=True)

    async def _reap_expired(self) -> None:
        now = time.monotonic()
        expired: list[_BrowserEntry] = []
        async with self._condition:
            expired = [
                entry
                for entry in self._entries
                if entry.leases == 0
                and (
                    now - entry.last_used >= self.idle_ttl
                    or now - entry.created_at >= self.max_age
                )
            ]
            for entry in expired:
                self._entries.remove(entry)
                entry.retiring = True
                entry.close_started = True
                self._closing_processes += 1
            if expired:
                self._condition.notify_all()
        await self._close_entries_safely(expired)

    async def _close_entries_safely(self, entries: list[_BrowserEntry]) -> None:
        """Every detached entry must finish cleanup, even if the reaper stops."""
        task = asyncio.gather(*(self._close_entry(entry) for entry in entries))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _reap_loop(self) -> None:
        interval = max(0.1, min(self.idle_ttl or 0.1, 30.0))
        try:
            while True:
                await asyncio.sleep(interval)
                await self._reap_expired()
        except asyncio.CancelledError:
            return

    async def close(self) -> None:
        """Close all processes and the shared Playwright controller."""
        async with self._condition:
            self._closing = True
            # A launch may have created a native browser but not registered
            # its entry yet. Keep the controller alive until that launch has
            # either registered or closed the browser.
            while self._launching:
                await self._condition.wait()
            entries = list(self._entries)
            self._entries.clear()
            for entry in entries:
                entry.retiring = True
                entry.close_started = True
            self._closing_processes += len(entries)
            self._condition.notify_all()
            reaper = self._reaper_task
            manager = self._playwright_manager
            self._reaper_task = None
            self._playwright_manager = None
            self._playwright = None
            self._started = False
        if reaper is not None:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
        await self._close_entries_safely(entries)
        async with self._condition:
            while self._closing_processes:
                await self._condition.wait()
        if manager is not None:
            try:
                await manager.__aexit__(None, None, None)
            except Exception:
                logger.debug("Playwright controller shutdown failed", exc_info=True)


_browser_pool = BrowserPool.from_env()


def get_browser_pool() -> BrowserPool:
    return _browser_pool


async def close_browser_pool() -> None:
    await _browser_pool.close()
