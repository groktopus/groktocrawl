"""Bounded, allowlisted browser metric relay over the existing control transport."""

import asyncio
import time

import httpx

from common.browser_metrics import MAX_METRICS_BYTES, parse_samples, render_samples

from .browser_client import browser_request_url, create_browser_client

COLLECTION_TIMEOUT_SECONDS = 2.0


class BrowserMetricsRelay:
    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = socket_path
        self.last_success = 0.0
        self._lock = asyncio.Lock()

    async def _fetch(self) -> str:
        async with create_browser_client(
            socket_path=self.socket_path, timeout=1.0
        ) as client:
            target = browser_request_url("/metrics", socket_path=self.socket_path)
            async with client.stream(
                "GET", target, headers={"Accept-Encoding": "identity"}
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("compressed browser metrics are not supported")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    body.extend(chunk)
                    if len(body) > MAX_METRICS_BYTES:
                        raise ValueError("browser metrics body too large")
                return render_samples(parse_samples(body.decode("utf-8")))

    async def collect(self) -> str:
        """No cached source samples and at most one bounded request per app."""
        success = 0
        fragment = ""
        if not self._lock.locked():
            async with self._lock:
                try:
                    async with asyncio.timeout(COLLECTION_TIMEOUT_SECONDS):
                        fragment = await self._fetch()
                    self.last_success = time.time()
                    success = 1
                except (TimeoutError, httpx.HTTPError, ValueError, UnicodeError):
                    pass
        # Last-success survives failures for freshness, but source samples don't.
        for name, help_text, value in (
            (
                "collection_success",
                "Whether this scrape collected a complete browser snapshot",
                success,
            ),
            (
                "last_success_timestamp_seconds",
                "Last valid browser snapshot, Unix seconds or zero if never",
                self.last_success,
            ),
        ):
            metric = f"groktocrawl_browser_metrics_{name}"
            fragment += f"# HELP {metric} {help_text}\n# TYPE {metric} gauge\n{metric} {value}\n"
        return fragment
