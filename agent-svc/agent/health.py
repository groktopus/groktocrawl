"""Health check probes for agent-svc dependencies.

Provides dependency health checks that probe each internal service
from agent-svc's perspective. Each probe returns a consistent dict:

    {"status": "ok"|"degraded"|"down", "latency_ms": float, "detail": str}
"""

import asyncio
import time
from typing import Any

import httpx


async def check_valkey(url: str) -> dict[str, Any]:
    """Probe Valkey via PING."""
    from redis import Redis

    start = time.monotonic()
    try:
        r = Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3
        )
        r.ping()
        r.close()
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "ok",
            "latency_ms": round(elapsed, 1),
            "detail": "Valkey PING ok",
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {"status": "down", "latency_ms": round(elapsed, 1), "detail": str(e)}


async def check_searxng(url: str) -> dict[str, Any]:
    """Probe SearXNG liveness without sending a real search query.

    Supports both backends:
    - SlopSearX (fixture/dev) exposes ``/health`` (server liveness +
      Valkey connectivity, zero external API cost).
    - Real SearXNG has no ``/health`` endpoint, so fall back to
      ``/config`` (static JSON, zero search cost).
    """
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # 1. SlopSearX-style /health (preferred, zero cost).
            resp = await client.get(
                f"{url.rstrip('/')}/health",
            )
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                return {
                    "status": "ok",
                    "latency_ms": round(elapsed, 1),
                    "detail": "SearXNG health ok",
                }
            # 2. Real SearXNG: /config returns static JSON, no search cost.
            if resp.status_code == 404:
                try:
                    cfg = await client.get(f"{url.rstrip('/')}/config")
                    elapsed = (time.monotonic() - start) * 1000
                    if cfg.status_code == 200:
                        return {
                            "status": "ok",
                            "latency_ms": round(elapsed, 1),
                            "detail": "SearXNG config ok",
                        }
                    if 300 <= cfg.status_code < 500:
                        return {
                            "status": "degraded",
                            "latency_ms": round(elapsed, 1),
                            "detail": f"SearXNG config returned HTTP {cfg.status_code} (expected 200)",
                        }
                    return {
                        "status": "down",
                        "latency_ms": round(elapsed, 1),
                        "detail": f"SearXNG config returned HTTP {cfg.status_code}",
                    }
                except Exception as e:
                    elapsed = (time.monotonic() - start) * 1000
                    return {
                        "status": "down",
                        "latency_ms": round(elapsed, 1),
                        "detail": f"SearXNG error: {e}",
                    }
            elapsed = (time.monotonic() - start) * 1000
            return {
                "status": "down",
                "latency_ms": round(elapsed, 1),
                "detail": f"SearXNG health returned HTTP {resp.status_code}",
            }
    except TimeoutError:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": "SearXNG connection timed out",
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": f"SearXNG error: {e}",
        }


async def check_scraper(url: str) -> dict[str, Any]:
    """Probe scraper-svc by hitting its /scrape endpoint with a trivial URL.

    Uses a GET to the scraper's root to check liveness without consuming
    real scraping resources.
    """
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url.rstrip('/')}/", timeout=10)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code < 500:
                return {
                    "status": "ok",
                    "latency_ms": round(elapsed, 1),
                    "detail": f"Scraper responded HTTP {resp.status_code}",
                }
            return {
                "status": "degraded",
                "latency_ms": round(elapsed, 1),
                "detail": f"Scraper returned HTTP {resp.status_code}",
            }
    except TimeoutError:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": "Scraper connection timed out",
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": f"Scraper error: {e}",
        }


async def check_browser(url: str) -> dict[str, Any]:
    """Probe browser-svc by hitting its root endpoint."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url.rstrip('/')}/", timeout=10)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code < 500:
                return {
                    "status": "ok",
                    "latency_ms": round(elapsed, 1),
                    "detail": f"Browser responded HTTP {resp.status_code}",
                }
            return {
                "status": "degraded",
                "latency_ms": round(elapsed, 1),
                "detail": f"Browser returned HTTP {resp.status_code}",
            }
    except TimeoutError:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": "Browser connection timed out",
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": f"Browser error: {e}",
        }


async def check_portal(url: str) -> dict[str, Any]:
    """Probe portal-svc by hitting its /health endpoint."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url.rstrip('/')}/health", timeout=10)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code < 500:
                return {
                    "status": "ok",
                    "latency_ms": round(elapsed, 1),
                    "detail": f"Portal responded HTTP {resp.status_code}",
                }
            return {
                "status": "degraded",
                "latency_ms": round(elapsed, 1),
                "detail": f"Portal returned HTTP {resp.status_code}",
            }
    except TimeoutError:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": "Portal connection timed out",
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {
            "status": "down",
            "latency_ms": round(elapsed, 1),
            "detail": f"Portal error: {e}",
        }


async def check_all(
    valkey_url: str = "redis://valkey:6379/0",
    searxng_url: str = "http://searxng:8080",
    scraper_url: str = "http://scraper-svc:8001",
    browser_url: str = "http://browser-svc:8012",
    portal_url: str = "http://portal-svc:8081",
) -> dict[str, Any]:
    """Probe all dependencies and return aggregated health.

    All probes run concurrently. The overall status is:
    - ``ok``: all dependencies healthy
    - ``degraded``: at least one dependency is degraded but none down
    - ``down``: at least one dependency is unreachable
    """
    results = await asyncio.gather(
        check_valkey(valkey_url),
        check_searxng(searxng_url),
        check_scraper(scraper_url),
        check_browser(browser_url),
        check_portal(portal_url),
        return_exceptions=True,
    )

    probes = {
        "valkey": results[0]
        if not isinstance(results[0], BaseException)
        else {"status": "error", "detail": str(results[0])},
        "searxng": results[1]
        if not isinstance(results[1], BaseException)
        else {"status": "error", "detail": str(results[1])},
        "scraper": results[2]
        if not isinstance(results[2], BaseException)
        else {"status": "error", "detail": str(results[2])},
        "browser": results[3]
        if not isinstance(results[3], BaseException)
        else {"status": "error", "detail": str(results[3])},
        "portal": results[4]
        if not isinstance(results[4], BaseException)
        else {"status": "error", "detail": str(results[4])},
    }

    statuses = [v["status"] for v in probes.values()]
    if any(s == "down" or s == "error" for s in statuses):
        overall = "down"
    elif any(s == "degraded" for s in statuses):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "checks": probes,
    }
