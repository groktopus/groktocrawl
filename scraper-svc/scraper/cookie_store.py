"""Cookie persistence for Cloudflare clearance cookies.

Stores and retrieves cf_clearance cookies via Valkey so that
Cloudflare challenges are solved once per domain per TTL window.

Ported from browser-svc/browser_svc/app.py.
Shares the same key prefix (cf:clearance:) and TTL (1500s).
"""

import contextlib
import json
import logging
import os
import time
from urllib.parse import urlparse

from .settings import load_settings

logger = logging.getLogger(__name__)

COOKIE_STORE_PREFIX = "cf:clearance:"
COOKIE_TTL_SECONDS = 1500  # 25 minutes (under Cloudflare's typical 30m expiry)

_redis_client = None  # Module-level lazy singleton


async def get_client():
    """Get or create the Valkey client singleton.

    Returns the client if connected, or None if Valkey is unavailable
    (graceful degradation — scraper continues without cookie persistence).
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    _cs_settings = load_settings()
    host = _cs_settings.valkey_host
    port = _cs_settings.valkey_port
    db = _cs_settings.valkey_db

    try:
        rpc_socket = os.environ.get("VALKEY_RPC_SOCKET")
        if rpc_socket:
            from .valkey_control import ValkeyControlClient

            _redis_client = ValkeyControlClient(rpc_socket)
            await _redis_client.ping()
            logger.info("Connected to restricted Valkey control socket for cookies")
            return _redis_client

        import redis.asyncio as aioredis

        _redis_client = aioredis.Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True,
        )
        await _redis_client.ping()
        logger.info("Connected to Valkey at %s:%s/%s for cookie store", host, port, db)
        return _redis_client
    except Exception as e:
        logger.warning(
            "Valkey unavailable at %s:%s — cookie persistence disabled (%s)",
            host,
            port,
            e,
        )
        _redis_client = None
        return None


async def close_client():
    """Close the Valkey client connection."""
    global _redis_client
    if _redis_client is not None:
        with contextlib.suppress(Exception):
            await _redis_client.close()
        _redis_client = None


def _cookie_key(url: str) -> str:
    """Extract TLD+1 domain for cookie scoping.

    Same algorithm used by browser-svc so cookies are shared across services.
    """
    hostname = urlparse(url).hostname or "unknown"
    parts = hostname.split(".")
    if len(parts) >= 2:
        domain = ".".join(parts[-2:])
    else:
        domain = parts[0]
    return f"{COOKIE_STORE_PREFIX}{domain}"


async def inject_cookies(url: str, context) -> None:
    """Inject stored Cloudflare clearance cookies into a Playwright context.

    Safe to call even if Valkey is unavailable — will silently no-op.
    """
    client = await get_client()
    if not client:
        return
    try:
        key = _cookie_key(url)
        stored = await client.get(key)
        if stored:
            data = json.loads(stored)
            await context.add_cookies(data["cookies"])
            remaining = data.get("ttl", 0) - (time.time() - data.get("resolved_at", 0))
            if remaining > 0:
                logger.info(
                    "Injected %d stored cookies for %s (%.0fs remaining)",
                    len(data["cookies"]),
                    url,
                    remaining,
                )
    except Exception as e:
        logger.debug("Cookie injection failed for %s: %s", url, e)


async def store_cookies(url: str, context) -> None:
    """Store Cloudflare clearance cookies from a Playwright context.

    Only stores cf_clearance cookies. Safe to call even if Valkey is
    unavailable — will silently no-op.
    """
    client = await get_client()
    if not client:
        return
    try:
        cookies = await context.cookies()
        cf_cookies = [c for c in cookies if c.get("name") == "cf_clearance"]
        if cf_cookies:
            key = _cookie_key(url)
            payload = json.dumps(
                {
                    "cookies": cf_cookies,
                    "resolved_at": time.time(),
                    "ttl": COOKIE_TTL_SECONDS,
                }
            )
            await client.setex(key, COOKIE_TTL_SECONDS, payload)
            logger.info("Stored %d cf_clearance cookies for %s", len(cf_cookies), url)
    except Exception as e:
        logger.debug("Cookie storage failed for %s: %s", url, e)
