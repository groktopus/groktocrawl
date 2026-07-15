"""Stealth configuration for Playwright browser automation.

Provides a reusable set of browser launch arguments, context settings, and
initialization scripts that mask headless Chromium automation signals.

Ported from browser-svc/browser_svc/app.py with additional fingerprinting
hardening (plugins, languages, chrome.runtime, WebGL).

Usage:
    from .stealth import create_stealth_browser, create_stealth_context

    async with async_playwright() as p:
        browser, cloakbrowser = await create_stealth_browser(p, url)
        context = await create_stealth_context(browser, cloakbrowser=cloakbrowser)
        page = await context.new_page()
"""

import asyncio
import hashlib
import logging
import random
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Real Chrome User-Agent ─────────────────────────────────────
REAL_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# ── Browser launch arguments ────────────────────────────────────
STEALTH_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# ── Browser context settings ────────────────────────────────────
STEALTH_CONTEXT_KWARGS = {
    "viewport": {"width": 1920, "height": 1080},
    "user_agent": REAL_CHROME_UA,
    "locale": "en-US",
    "timezone_id": "America/New_York",
    "permissions": ["geolocation"],
}

# ── Init script to hide automation signals ─────────────────────
STEALTH_INIT_SCRIPT = """() => {
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
}"""


def fingerprint_seed(url: str) -> str:
    """Return a stable, non-secret fingerprint value for a normalized domain."""
    host = (urlparse(url).hostname or url).lower().removeprefix("www.")
    return str(
        int.from_bytes(hashlib.sha256(host.encode("utf-8")).digest()[:4], "big")
        & 0x7FFFFFFF
    )


async def create_stealth_browser(playwright, url: str = ""):
    """Launch a Chromium browser with stealth configuration.

    Args:
        playwright: An async_playwright instance.

    Returns:
        ``(browser, cloakbrowser)`` where the flag selects matching context defaults.
    """
    logger.debug("Launching stealth Chromium browser")
    try:
        from cloakbrowser import ensure_binary, get_default_stealth_args

        executable_path = await asyncio.to_thread(ensure_binary)
        args = [
            arg
            for arg in get_default_stealth_args()
            if not arg.startswith("--fingerprint=")
        ]
        args.append(f"--fingerprint={fingerprint_seed(url)}")
        return await playwright.chromium.launch(
            headless=True, executable_path=str(executable_path), args=args
        ), True
    except Exception as exc:
        logger.warning(
            "CloakBrowser unavailable; using stock Playwright Chromium: %s", exc
        )
        return await playwright.chromium.launch(
            headless=True, args=STEALTH_BROWSER_ARGS
        ), False


async def create_stealth_context(browser, cloakbrowser: bool = False, **kwargs):
    """Create a browser context with stealth settings.

    Args:
        browser: A Browser instance from create_stealth_browser.
        **kwargs: Additional keyword arguments forwarded to browser.new_context()
            (e.g., proxy config for context-level proxy assignment).

    Returns:
        A BrowserContext with stock-fallback fingerprint settings, or the
        upstream CloakBrowser defaults when ``cloakbrowser`` is true.
    """
    logger.debug("Creating stealth browser context")
    if cloakbrowser:
        return await browser.new_context(**kwargs)
    context_kwargs = {
        **STEALTH_CONTEXT_KWARGS,
        "viewport": {
            "width": 1920 + random.randint(-5, 5),
            "height": 1080 + random.randint(-5, 5),
        },
        **kwargs,
    }
    context = await browser.new_context(**context_kwargs)
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return context
