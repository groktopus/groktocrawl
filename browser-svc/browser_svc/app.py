"""Headless browser session management service.

Manages Playwright browser sessions with TTL-based lifecycle.
Each session is an isolated Chromium instance with its own context.
"""

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from playwright.async_api import async_playwright
from pydantic import BaseModel

from common.browser_metrics import append_metrics
from common.logging import setup_logging
from common.metrics import METRICS
from common.middleware import add_request_id_middleware
from common.stage_metrics import inc_counter, set_gauge
from common.url import extract_domain, is_private_host

from . import lifecycle
from .capacity_metrics import CapacityMetrics
from .cookie_rpc import DEFAULT_COOKIE_RPC_SOCKET, CookieRPCClient
from .lifecycle import BrowserOwner
from .process_health import process_capacity
from .settings import load_settings

setup_logging()
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
    if "ddos-guard" in url.lower() or "/.well-known/ddos-guard" in url.lower():  # noqa: SIM103
        return True
    return False


def _protected_renderer_enabled(env: Mapping[str, str] | None = None) -> bool:
    settings = os.environ if env is None else env
    value = settings.get("BROWSER_PROTECTED_RENDERER", "").lower()
    if value not in {"", "0", "1", "false", "true"}:
        raise RuntimeError("protected renderer setting is invalid")
    return value in {"1", "true"}


def _protected_navigation_allowed(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme.lower() in {"http", "https", "data"} or (
        parsed.scheme.lower() == "about" and parsed.path == "blank"
    )


def _chromium_launch_options(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return launch options, requiring an explicit proxy in protected mode.

    This configures Chromium's proxy only. Network-namespace isolation and
    qualification of browser side channels remain deployment responsibilities.
    """
    settings = os.environ if env is None else env
    protected = _protected_renderer_enabled(settings)
    proxy_url = settings.get("BROWSER_CAPTURE_PROXY_URL", "").strip()
    if protected and not proxy_url:
        raise RuntimeError("protected renderer requires capture proxy")

    options: dict[str, Any] = {
        "headless": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if proxy_url:
        parsed = urlsplit(proxy_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("capture proxy URL is invalid") from exc
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not 1 <= port <= 65535
            or "%" in parsed.netloc
        ):
            raise RuntimeError("capture proxy URL is invalid")
        options["proxy"] = {"server": f"http://{parsed.netloc}"}
    return options


app = FastAPI(title="GroktoCrawl Browser Service", version="0.1.0")

# ── Instrumentation ──────────────────────────────────────────
add_request_id_middleware(app)
METRICS.counter("browser_sessions_created_total", "Total browser sessions created")
METRICS.counter("browser_sessions_expired_total", "Total browser sessions expired")

_ACTIVE_SESSIONS = "groktocrawl_browser_active_sessions"
_ACTIVE_SESSIONS_HELP = "Currently active browser sessions"
_DESTROYED_TOTAL = "groktocrawl_browser_sessions_destroyed_total"
_DESTROYED_TOTAL_HELP = "Browser sessions destroyed by reason"


def _update_active_sessions_gauge() -> None:
    set_gauge(_ACTIVE_SESSIONS, _ACTIVE_SESSIONS_HELP, {}, float(len(_sessions)))


def _record_session_destroyed(reason: str) -> None:
    inc_counter(_DESTROYED_TOTAL, _DESTROYED_TOTAL_HELP, {"reason": reason})


# In-memory session store
_sessions: dict[str, "SessionData"] = {}
_CLEANUP_INTERVAL = 30  # seconds
_owners: dict[str, BrowserOwner] = {}
_cleanup_task: asyncio.Task | None = None
_shutting_down = False
_update_active_sessions_gauge()
_capacity_metrics = CapacityMetrics()


class SessionData:
    """Holds Playwright objects for a single session."""

    def __init__(self, browser, context, page, ttl: int, playwright):
        self.browser = browser
        self.context = context
        self.page = page
        self.playwright = playwright
        self.created_at = time.time()
        self.ttl = ttl
        self.last_used = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at >= self.ttl

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


def _navigation_response_fields(response: Any | None) -> dict[str, Any]:
    """Return bounded, non-body metadata for a completed browser navigation."""
    if response is None:
        return {}
    fields: dict[str, Any] = {}
    status = getattr(response, "status", None)
    if type(status) is int and 100 <= status <= 599:
        fields["http_status"] = status
    raw_content_type = getattr(response, "headers", {}).get("content-type", "")
    if not isinstance(raw_content_type, str):
        return fields
    media_type = raw_content_type.split(";", 1)[0].strip().lower()
    token = r"[a-z0-9!#$%&'*+.^_`|~-]{1,64}"
    if len(media_type) <= 129 and re.fullmatch(rf"{token}/{token}", media_type):
        fields["content_type"] = media_type
    return fields


class BrowserListResponse(BaseModel):
    success: bool = True
    sessions: list[dict] = []


@app.on_event("startup")
async def startup():
    global _cleanup_task, _shutting_down
    _shutting_down = False
    if _protected_renderer_enabled():
        if os.environ.get("BROWSER_CAPTURE_FIREWALL_READY") != "1":
            raise RuntimeError("protected renderer firewall is not active")
        cookie_socket = os.environ.get(
            "BROWSER_COOKIE_RPC_SOCKET", DEFAULT_COOKIE_RPC_SOCKET
        )
        app.state.redis = CookieRPCClient(cookie_socket)
        logger.info("Protected renderer started with private cookie RPC")
        _cleanup_task = asyncio.create_task(_cleanup_loop())
        return

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
    _cleanup_task = asyncio.create_task(_cleanup_loop())


async def _cleanup_loop():
    """Periodically remove expired sessions."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        expired = [sid for sid, s in _sessions.items() if s.expired]
        for sid in expired:
            logger.info("Cleaning up expired session %s", sid)
            await _destroy_session(sid, reason="expired")


async def _retire_owner(session_id: str, owner: BrowserOwner) -> None:
    if await owner.close():
        _owners.pop(session_id, None)
    else:
        owner.phase = "quarantined"
        _capacity_metrics.increment("cleanup_incomplete_total")
        logger.error("Browser cleanup incomplete; admission disabled until restart")


def _begin_cleanup(session_id: str, owner: BrowserOwner) -> asyncio.Task:
    if owner.cleanup is None:
        owner.phase = "closing"
        owner.cleanup = asyncio.create_task(_retire_owner(session_id, owner))
    return owner.cleanup


async def _destroy_session(session_id: str, reason: str = "deleted") -> None:
    """Retire a session once, retaining capacity until cleanup has completed."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return
    _record_session_destroyed(reason)
    if reason == "expired":
        METRICS.counter(
            "browser_sessions_expired_total", "Total browser sessions expired"
        ).inc()
    _update_active_sessions_gauge()
    owner = _owners.get(session_id)
    if owner is None:
        owner = BrowserOwner(
            page=session.page,
            context=session.context,
            browser=session.browser,
            playwright=session.playwright,
        )
        _owners[session_id] = owner
    await asyncio.shield(_begin_cleanup(session_id, owner))


@app.on_event("shutdown")
async def shutdown():
    global _shutting_down
    _shutting_down = True
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        await asyncio.gather(_cleanup_task, return_exceptions=True)
    for session_id in list(_sessions):
        # Schedule every retirement before awaiting any of them.
        session = _sessions.pop(session_id)
        _record_session_destroyed("shutdown")
        owner = _owners.setdefault(
            session_id,
            BrowserOwner(
                page=session.page,
                context=session.context,
                browser=session.browser,
                playwright=session.playwright,
            ),
        )
        _begin_cleanup(session_id, owner)
    _update_active_sessions_gauge()
    tasks = [_begin_cleanup(sid, owner) for sid, owner in list(_owners.items())]
    if tasks:
        await asyncio.gather(*(asyncio.shield(task) for task in tasks))
    redis = getattr(app.state, "redis", None)
    close = getattr(redis, "aclose", None)
    if close is not None:
        owner = BrowserOwner()
        await owner.bounded(close(), lifecycle.CLOSE_TIMEOUT)


def _admission_reason(capacity: dict) -> str | None:
    if _shutting_down:
        return "shutting_down"
    if any(owner.phase == "quarantined" for owner in _owners.values()):
        return "cleanup_incomplete"
    if any(owner.phase == "creating" for owner in _owners.values()):
        return "creation_in_progress"
    if len(_owners) >= lifecycle.MAX_SESSIONS:
        return "session_capacity_reached"
    if capacity["state"] == "degraded":
        return "process_capacity_low"
    return None


@app.get("/health")
async def health():
    capacity = process_capacity()
    reason = _admission_reason(capacity)
    degraded = reason is not None
    payload = {
        "status": "degraded" if degraded else "ok",
        "active_sessions": len(_sessions),
        "process_capacity": capacity,
        "admission": {
            "state": "unavailable" if degraded else "available",
            "reason": reason,
            "creating": sum(o.phase == "creating" for o in _owners.values()),
            "closing": sum(o.phase == "closing" for o in _owners.values()),
            "quarantined": sum(o.phase == "quarantined" for o in _owners.values()),
            "reserved": len(_owners),
            "limit": lifecycle.MAX_SESSIONS,
        },
    }
    return JSONResponse(payload, status_code=503 if degraded else 200)


@app.get("/metrics")
async def metrics():
    """Prometheus-compatible OpenMetrics endpoint."""
    capacity = process_capacity()
    snapshot = _capacity_metrics.render(
        capacity=capacity,
        phases=(owner.phase for owner in _owners.values()),
        limit=lifecycle.MAX_SESSIONS,
        admission=_admission_reason(capacity) is None,
    )
    _update_active_sessions_gauge()
    return PlainTextResponse(
        append_metrics(METRICS.generate_openmetrics(), snapshot),
        media_type="application/openmetrics-text; version=1.0.0",
    )


async def _initialize_browser(owner: BrowserOwner) -> None:
    await owner.start()
    owner.browser = await owner.playwright.chromium.launch(**_chromium_launch_options())
    owner.context = await owner.browser.new_context(
        viewport={
            "width": 1920 + random.randint(-5, 5),
            "height": 1080 + random.randint(-5, 5),
        },
        user_agent=REAL_CHROME_UA,
        locale="en-US",
        timezone_id="America/New_York",
        permissions=["geolocation"],
    )
    owner.page = await owner.context.new_page()
    # Hide Playwright automation signals from bot detection
    await owner.page.add_init_script("""() => {
        // Override navigator properties
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        // Real Chrome reports 5 plugins (Chrome PDF Plugin, Chrome PDF Viewer, Native Client, etc.)
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' },
            ],
        });

        // Real Chrome reports these languages
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

        // Typical modern hardware concurrency (8 cores is most common)
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

        // Add chrome.runtime (real Chrome has it, headless Playwright doesn't)
        if (window.chrome) {
            window.chrome.runtime = {};
        }

        // Override WebGL vendor/renderer to look like a real GPU
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37445) {
                return 'Intel Inc.';
            }
            // UNMASKED_RENDERER_WEBGL
            if (parameter === 37446) {
                return 'Intel Iris OpenGL Engine';
            }
            return getParameter.call(this, parameter);
        };
    }""")


@app.post("/browsers", response_model=BrowserCreateResponse)
async def create_browser(req: BrowserCreateRequest):
    """Reserve capacity before starting work and publish only complete sessions."""
    reason = _admission_reason(process_capacity())
    if reason is not None:
        _capacity_metrics.increment("admission_rejections_total", reason)
        raise HTTPException(status_code=503, detail=reason)
    session_id = str(uuid.uuid4())
    owner = BrowserOwner()
    _owners[session_id] = owner
    try:
        owner.manager = async_playwright()
        owner.creation = asyncio.create_task(_initialize_browser(owner))
        done, _ = await asyncio.wait({owner.creation}, timeout=lifecycle.CREATE_TIMEOUT)
        if not done:
            raise HTTPException(status_code=504, detail="Browser creation timed out")
        owner.creation.result()
        if _shutting_down or owner.phase != "creating":
            raise HTTPException(status_code=503, detail="Browser service is stopping")
        _sessions[session_id] = SessionData(
            owner.browser, owner.context, owner.page, req.ttl, owner.playwright
        )
        owner.phase = "active"
        METRICS.counter(
            "browser_sessions_created_total", "Total browser sessions created"
        ).inc()
        _update_active_sessions_gauge()
        logger.info("Created browser session %s (TTL: %ds)", session_id, req.ttl)
        return BrowserCreateResponse(id=session_id)
    except BaseException as exc:
        failure_reason = "error"
        if isinstance(exc, asyncio.CancelledError):
            failure_reason = "cancelled"
        elif isinstance(exc, HTTPException) and exc.status_code == 504:
            failure_reason = "timeout"
        _capacity_metrics.increment("creation_failures_total", failure_reason)
        cleanup = _begin_cleanup(session_id, owner)
        # Cleanup is independently owned; repeated request cancellation cannot
        # abandon it or free its capacity reservation prematurely.
        await asyncio.shield(cleanup)
        if isinstance(exc, (asyncio.CancelledError, HTTPException)):
            raise
        if not isinstance(exc, Exception):
            raise
        logger.error("Failed to create browser session: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to create browser session: {exc}"
        ) from exc


@app.post("/browsers/{session_id}/execute", response_model=BrowserExecuteResponse)
async def execute_action(session_id: str, req: BrowserExecuteRequest):
    """Execute an action in an existing browser session."""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if session.expired:
        await _destroy_session(session_id, reason="expired")
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
            protected_renderer = _protected_renderer_enabled()
            if protected_renderer and not _protected_navigation_allowed(req.url):
                raise HTTPException(
                    status_code=400,
                    detail="Navigation scheme is not allowed in protected renderer",
                )
            if not protected_renderer and is_private_host(req.url):
                raise HTTPException(
                    status_code=400,
                    detail="Navigation to private or internal destination blocked",
                )
            # Inject stored Cloudflare clearance cookies before navigation
            redis_client = getattr(app.state, "redis", None)
            await _inject_cookies(req.url, session.context, redis_client)

            navigation_response = await page.goto(
                req.url, wait_until="networkidle", timeout=req.timeout
            )
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

            result = {"url": current_url, "title": title}
            # Keep navigation success semantics unchanged while exposing only
            # bounded response metadata. Protected-boundary probes use this to
            # distinguish a gateway denial from a successful navigation to a
            # private controller endpoint; response bodies and headers stay private.
            result.update(_navigation_response_fields(navigation_response))
            return BrowserExecuteResponse(result=result)

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
            await _destroy_session(sid, reason="expired")
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
