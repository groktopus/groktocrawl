"""Headless browser session management service.

Manages Playwright browser sessions with TTL-based lifecycle.
Each session is an isolated Chromium instance with its own context.
"""

import asyncio
import json
import logging
import socket
import time
import uuid
from ipaddress import ip_address, ip_network
from typing import Any

from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel

from common.url import extract_domain

from .settings import load_settings

logger = logging.getLogger(__name__)

# ── Cookie persistence ─────────────────────────────────────────
COOKIE_STORE_PREFIX = "cf:clearance:"
COOKIE_TTL_SECONDS = 1500  # 25 minutes (under Cloudflare's typical 30m expiry)


def _cookie_key(url: str) -> str:
    """Extract TLD+1 domain for cookie scoping."""
    hostname = extract_domain(url) or "unknown"
    parts = hostname.split(".")
    if len(parts) >= 2:
        domain = ".".join(parts[-2:])
    else:
        domain = parts[0]
    return f"{COOKIE_STORE_PREFIX}{domain}"


async def _inject_cookies(url: str, context, redis_client) -> None:
    """Inject stored Cloudflare clearance cookies before navigation."""
    if not redis_client:
        return
    try:
        key = _cookie_key(url)
        stored = await redis_client.get(key)
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


async def _store_cookies(url: str, context, redis_client) -> None:
    """Store Cloudflare clearance cookies after successful navigation."""
    if not redis_client:
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
            await redis_client.setex(key, COOKIE_TTL_SECONDS, payload)
            logger.info("Stored %d cf_clearance cookies for %s", len(cf_cookies), url)
    except Exception as e:
        logger.debug("Cookie storage failed for %s: %s", url, e)


# ── Stealth configuration ─────────────────────────────────────
# Real Chrome user agent to avoid bot detection
REAL_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

CLOUDFLARE_INDICATORS = [
    "Just a moment...",
    "Checking your browser",
    "DDoS protection by",
    "cf-browser-verification",
    "challenge-platform",
]

DDOS_GUARD_INDICATORS = [
    "DDoS-Guard",
    "DDOS-GUARD",
    "ddos-guard",
    "Checking your browser before accessing",
    ".well-known/ddos-guard",
]


def _is_bot_challenge(title: str, url: str) -> bool:
    """Heuristic: does the page indicate a bot challenge (Cloudflare or DDoS-Guard)?"""
    # Cloudflare checks
    for indicator in CLOUDFLARE_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    if "cf_chl" in url.lower() or "challenge-platform" in url.lower():
        return True
    # DDoS-Guard checks
    for indicator in DDOS_GUARD_INDICATORS:
        if indicator.lower() in title.lower():
            return True
    if "ddos-guard" in url.lower() or "/.well-known/ddos-guard" in url.lower():
        return True
    return False


# ── Private IP / SSRF protection ─────────────────────────────────

# Private and special-purpose IP ranges that should never be navigated to
_PRIVATE_NETWORKS = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),  # loopback
    ip_network("::1/128"),  # IPv6 loopback
    ip_network("169.254.0.0/16"),  # link-local
    ip_network("0.0.0.0/8"),  # "this" network
    ip_network("100.64.0.0/10"),  # Carrier-grade NAT (RFC 6598)
    ip_network("198.18.0.0/15"),  # Benchmarking (RFC 2544)
    ip_network("240.0.0.0/4"),  # Reserved / future use
]

_METADATA_IPS = {
    ip_address("169.254.169.254"),  # AWS/GCP/Azure metadata
    ip_address("fd00:ec2::254"),  # AWS IMDSv2 IPv6
}

# Docker-internal hostnames that resolve to the host machine
_PRIVATE_HOSTNAME_SUFFIXES = [
    ".docker.internal",
]


def _resolve_to_ips(hostname: str) -> list:
    """Resolve a hostname to all IP addresses (IPv4 and IPv6)."""
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
        ips = set()
        for family, _, _, _, sockaddr in addrinfo:
            try:
                ips.add(ip_address(sockaddr[0]))
            except ValueError:
                continue
        return list(ips)
    except socket.gaierror:
        return []


def _is_private_url(url: str) -> tuple[bool, str]:
    """Check if a URL targets a private/internal IP or hostname.

    Returns (is_private, reason) tuple.

    Delegates to the shared ``common.url.is_private_host``
    for the actual check, then maps the boolean result back to the
    ``(bool, str)`` tuple format.
    """
    from common.url import is_private_host as _shared_is_private

    return (True, "Private or internal URL") if _shared_is_private(url) else (False, "")


app = FastAPI(title="GroktoCrawl Browser Service", version="0.1.0")

# In-memory session store
_sessions: dict[str, "SessionData"] = {}
_CLEANUP_INTERVAL = 30  # seconds


class SessionData:
    """Holds Playwright objects for a single session."""

    def __init__(self, browser, context, page, ttl: int):
        self.browser = browser
        self.context = context
        self.page = page
        self.created_at = time.time()
        self.ttl = ttl
        self.last_used = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used


class BrowserCreateRequest(BaseModel):
    ttl: int = 300  # seconds, default 5 minutes


class BrowserExecuteRequest(BaseModel):
    action: str  # navigate, click, type, screenshot, scroll, wait, getContent, executeScript
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    script: str | None = None
    timeout: int = 10000


class BrowserCreateResponse(BaseModel):
    success: bool = True
    id: str


class BrowserExecuteResponse(BaseModel):
    success: bool = True
    result: Any = None
    error: str | None = None


class BrowserListResponse(BaseModel):
    success: bool = True
    sessions: list[dict] = []


@app.on_event("startup")
async def startup():
    # Connect to Valkey/Redis for cookie persistence
    _br_settings = load_settings()
    valkey_host = _br_settings.valkey_host
    valkey_port = _br_settings.valkey_port
    try:
        import redis.asyncio as aioredis

        app.state.redis = aioredis.Redis(
            host=valkey_host,
            port=valkey_port,
            decode_responses=True,
        )
        await app.state.redis.ping()
        logger.info("Connected to Valkey at %s:%s", valkey_host, valkey_port)
    except Exception as e:
        logger.warning(
            "Valkey not available at %s:%s — cookie persistence disabled (%s)",
            valkey_host,
            valkey_port,
            e,
        )
        app.state.redis = None
    asyncio.create_task(_cleanup_loop())


async def _cleanup_loop():
    """Periodically remove expired sessions."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        expired = [sid for sid, s in _sessions.items() if s.expired]
        for sid in expired:
            logger.info("Cleaning up expired session %s", sid)
            await _destroy_session(sid)


async def _destroy_session(session_id: str) -> None:
    """Close and remove a browser session."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return
    try:
        await session.page.close()
        await session.context.close()
        await session.browser.close()
    except Exception as e:
        logger.warning("Error closing session %s: %s", session_id, e)


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(_sessions)}


@app.post("/browsers", response_model=BrowserCreateResponse)
async def create_browser(req: BrowserCreateRequest):
    """Create a new headless browser session."""
    session_id = str(uuid.uuid4())

    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=REAL_CHROME_UA,
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation"],
        )
        page = await context.new_page()
        # Hide Playwright automation signals from bot detection
        await page.add_init_script("""() => {
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        }""")
        session = SessionData(browser, context, page, req.ttl)
        _sessions[session_id] = session
        logger.info("Created browser session %s (TTL: %ds)", session_id, req.ttl)
        return BrowserCreateResponse(id=session_id)
    except Exception as e:
        logger.error("Failed to create browser session: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to create browser session: {e}"
        )


@app.post("/browsers/{session_id}/execute", response_model=BrowserExecuteResponse)
async def execute_action(session_id: str, req: BrowserExecuteRequest):
    """Execute an action in an existing browser session."""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if session.expired:
        await _destroy_session(session_id)
        raise HTTPException(status_code=404, detail="Session expired")

    session.last_used = time.time()
    page = session.page

    try:
        if req.action == "navigate":
            if not req.url:
                raise HTTPException(
                    status_code=400, detail="url required for navigate action"
                )
            # Security: reject private/internal destination URLs
            is_private, reason = _is_private_url(req.url)
            if is_private:
                raise HTTPException(
                    status_code=400,
                    detail=f"Navigation to private or internal destination blocked: {reason}",
                )
            # Inject stored Cloudflare clearance cookies before navigation
            redis_client = getattr(app.state, "redis", None)
            await _inject_cookies(req.url, session.context, redis_client)

            await page.goto(req.url, wait_until="networkidle", timeout=req.timeout)
            # Bot challenge detection (Cloudflare / DDoS-Guard) — wait for JS challenge to resolve
            title = await page.title()
            current_url = page.url
            if _is_bot_challenge(title, current_url):
                logger.info(
                    "Cloudflare challenge detected on %s, waiting for resolution...",
                    req.url,
                )
                await page.wait_for_timeout(8000)
                title = await page.title()
                current_url = page.url
                if _is_bot_challenge(title, current_url):
                    logger.warning("Bot challenge persisted after wait for %s", req.url)

            # Store Cloudflare cookies after successful navigation
            await _store_cookies(req.url, session.context, redis_client)

            return BrowserExecuteResponse(result={"url": current_url, "title": title})

        elif req.action == "click":
            if not req.selector:
                raise HTTPException(
                    status_code=400, detail="selector required for click action"
                )
            await page.click(req.selector, timeout=req.timeout)
            return BrowserExecuteResponse(result={"clicked": req.selector})

        elif req.action == "type":
            if not req.selector or req.text is None:
                raise HTTPException(
                    status_code=400, detail="selector and text required for type action"
                )
            await page.fill(req.selector, req.text, timeout=req.timeout)
            return BrowserExecuteResponse(result={"typed": req.selector})

        elif req.action == "screenshot":
            buf = await page.screenshot(full_page=True)
            import base64

            b64 = base64.b64encode(buf).decode()
            return BrowserExecuteResponse(result={"screenshot": b64, "format": "png"})

        elif req.action == "scroll":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            return BrowserExecuteResponse(result={"scrolled": True})

        elif req.action == "wait":
            if req.selector:
                await page.wait_for_selector(req.selector, timeout=req.timeout)
            else:
                await asyncio.sleep(min(req.timeout / 1000, 5))
            return BrowserExecuteResponse(result={"waited": True})

        elif req.action == "getContent":
            html = await page.content()
            title = await page.title()
            url = page.url
            return BrowserExecuteResponse(
                result={"url": url, "title": title, "html_length": len(html)}
            )

        elif req.action == "executeScript":
            if not req.script:
                raise HTTPException(
                    status_code=400, detail="script required for executeScript action"
                )
            result = await page.evaluate(req.script)
            return BrowserExecuteResponse(result={"script_result": result})

        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Action %s failed in session %s: %s", req.action, session_id, e)
        return BrowserExecuteResponse(success=False, error=str(e))


@app.get("/browsers", response_model=BrowserListResponse)
async def list_browsers():
    """List all active browser sessions."""
    sessions = []
    for sid, s in list(_sessions.items()):
        if s.expired:
            await _destroy_session(sid)
        else:
            sessions.append(
                {
                    "id": sid,
                    "age_seconds": int(time.time() - s.created_at),
                    "ttl": s.ttl,
                    "idle_seconds": int(s.idle_seconds),
                }
            )
    return BrowserListResponse(sessions=sessions)


@app.delete("/browsers/{session_id}", response_model=dict)
async def destroy_browser(session_id: str):
    """Destroy a browser session."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    await _destroy_session(session_id)
    return {"success": True, "id": session_id}
